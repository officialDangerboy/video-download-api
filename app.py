"""
Video Download API - Enhanced Version
Features: Audio detection, MP3 support, automatic file cleanup, file size info
"""

from flask import Flask, request, send_file, jsonify, after_this_request
from flask_cors import CORS
import os
import re
from datetime import datetime
import threading
import uuid
import subprocess
import json
import time

app = Flask(__name__)
CORS(app)

# Configuration
app.config['DOWNLOAD_FOLDER'] = './downloads'
app.config['COOKIES_FILE'] = './cookies/youtube_cookies.txt'

os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)
os.makedirs('./cookies', exist_ok=True)
FFMPEG_PATH = "ffmpeg"

# Storage
download_tasks = {}


def cleanup_old_files():
    """Delete files older than 5 minutes"""
    try:
        download_folder = app.config['DOWNLOAD_FOLDER']
        current_time = time.time()
        
        for filename in os.listdir(download_folder):
            filepath = os.path.join(download_folder, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 300:  # 5 minutes
                    os.remove(filepath)
                    print(f"Cleaned up old file: {filename}")
    except Exception as e:
        print(f"Cleanup error: {str(e)}")


def get_platform(url):
    if re.search(r'(youtube\.com|youtu\.be)', url):
        return "youtube"
    elif re.search(r'instagram\.com', url):
        return "instagram"
    return None


def format_filesize(bytes_size):
    """Convert bytes to human readable format"""
    if not bytes_size:
        return None
    
    # Convert to MB
    mb_size = bytes_size / (1024 * 1024)
    
    if mb_size < 1:
        # Less than 1 MB, show in KB
        kb_size = bytes_size / 1024
        return f"{kb_size:.1f} KB"
    elif mb_size < 1024:
        # Show in MB
        return f"{mb_size:.1f} MB"
    else:
        # Show in GB
        gb_size = mb_size / 1024
        return f"{gb_size:.2f} GB"


def get_video_info_universal(url, platform):
    try:
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--no-warnings',
            '--no-check-certificates',
            url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            raise Exception("Video not accessible")

        info = json.loads(result.stdout)

        # -----------------------------
        # Duration
        # -----------------------------
        duration_seconds = int(info.get('duration') or 0)
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        duration_formatted = f"{minutes}:{seconds:02d}"

        # -----------------------------
        # Views
        # -----------------------------
        view_count = int(info.get('view_count') or 0)

        if view_count >= 1_000_000:
            views_formatted = f"{view_count / 1_000_000:.1f}M views"
        elif view_count >= 1_000:
            views_formatted = f"{view_count / 1_000:.1f}K views"
        else:
            views_formatted = f"{view_count} views" if view_count else ""

        # -----------------------------
        # Thumbnail (LOCAL DOWNLOAD)
        # -----------------------------
        video_id = str(uuid.uuid4())[:8]
        local_thumb_filename = download_thumbnail(url, video_id)

        if local_thumb_filename:
            thumbnail_url = f"/api/thumbnail/{local_thumb_filename}"
        else:
            # fallback (temporary)
            thumbnail_url = info.get("thumbnail", "")

        # -----------------------------
        # Get File Sizes from formats
        # -----------------------------
        all_formats = info.get('formats', [])
        
        # Find sizes for specific quality levels
        size_1080p = None
        size_720p = None
        size_best = None
        size_audio = None
        
        for f in all_formats:
            height = f.get('height')
            filesize = f.get('filesize') or f.get('filesize_approx')
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            
            # Best quality (highest resolution video with audio)
            if vcodec != 'none' and acodec != 'none' and filesize:
                if not size_best or (height and height > (size_best.get('height') or 0)):
                    size_best = {'size': filesize, 'height': height}
            
            # 1080p
            if height == 1080 and vcodec != 'none' and filesize:
                if not size_1080p or (acodec != 'none'):  # Prefer with audio
                    size_1080p = filesize
            
            # 720p
            if height == 720 and vcodec != 'none' and filesize:
                if not size_720p or (acodec != 'none'):  # Prefer with audio
                    size_720p = filesize
            
            # Audio only
            if vcodec == 'none' and acodec != 'none' and filesize:
                if not size_audio or filesize > size_audio:  # Get best audio
                    size_audio = filesize

        # -----------------------------
        # Format Options with Sizes
        # -----------------------------
        if platform == "youtube":
            formats = [
                {
                    "quality": "Best Quality",
                    "format_id": "best",
                    "ext": "mp4",
                    "type": "video",
                    "has_audio": True,
                    "filesize": size_best['size'] if size_best else None,
                    "filesize_formatted": format_filesize(size_best['size']) if size_best else "Unknown"
                },
                {
                    "quality": "1080p",
                    "format_id": "best[height<=1080]",
                    "ext": "mp4",
                    "type": "video",
                    "has_audio": True,
                    "filesize": size_1080p,
                    "filesize_formatted": format_filesize(size_1080p) if size_1080p else "Unknown"
                },
                {
                    "quality": "720p",
                    "format_id": "best[height<=720]",
                    "ext": "mp4",
                    "type": "video",
                    "has_audio": True,
                    "filesize": size_720p,
                    "filesize_formatted": format_filesize(size_720p) if size_720p else "Unknown"
                },
                {
                    "quality": "Audio Only",
                    "format_id": "bestaudio",
                    "ext": "mp3",
                    "type": "audio",
                    "has_audio": True,
                    "filesize": size_audio,
                    "filesize_formatted": format_filesize(size_audio) if size_audio else "Unknown"
                }
            ]
        else:
            formats = [
                {
                    "quality": "Best Quality",
                    "format_id": "best",
                    "ext": "mp4",
                    "type": "video",
                    "has_audio": True,
                    "filesize": size_best['size'] if size_best else None,
                    "filesize_formatted": format_filesize(size_best['size']) if size_best else "Unknown"
                },
                {
                    "quality": "Audio Only",
                    "format_id": "bestaudio",
                    "ext": "mp3",
                    "type": "audio",
                    "has_audio": True,
                    "filesize": size_audio,
                    "filesize_formatted": format_filesize(size_audio) if size_audio else "Unknown"
                }
            ]

        return {
            "title": info.get("title", "Video"),
            "thumbnail": thumbnail_url,
            "duration": duration_formatted,
            "views": views_formatted,
            "uploader": info.get("uploader", ""),
            "platform": platform,
            "formats": formats,
            "has_audio": True
        }

    except Exception as e:
        raise Exception(str(e))


def download_thumbnail(url, video_id):
    try:
        output_base = os.path.join(app.config['DOWNLOAD_FOLDER'], video_id)

        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-thumbnail",
            "--convert-thumbnails", "jpg",
            "--output", output_base,
            url
        ]

        subprocess.run(cmd, capture_output=True)

        # Find generated thumbnail
        for file in os.listdir(app.config['DOWNLOAD_FOLDER']):
            if file.startswith(video_id) and file.endswith(".jpg"):
                return file  # return filename only

        return None

    except Exception as e:
        print("Thumbnail download error:", str(e))
        return None


def download_youtube_video(url, format_id, task_id, output_path, is_audio=False):

    try:
        download_tasks[task_id]['status'] = 'downloading'
        download_tasks[task_id]['progress'] = 0

        cookie_path = app.config['COOKIES_FILE']
        use_cookies = os.path.exists(cookie_path)
        output_template = output_path + ".%(ext)s"

        # --------------------------
        # BUILD COMMAND
        # --------------------------

        if is_audio:
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "--format", "bestaudio",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "192K",
                "--prefer-ffmpeg",
                "--output", output_template,
                url
            ]
        else:
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "--format", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                "--merge-output-format", "mp4",
                "--prefer-ffmpeg",
                "--output", output_template,
                url
            ]

        # Add cookies ONLY if file exists
        if use_cookies:
            cmd.insert(1, "--cookies")
            cmd.insert(2, cookie_path)

        print("\nStarting download:")
        print(" ".join(cmd[:10]), "...")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )

        # --------------------------
        # PROGRESS TRACKING
        # --------------------------
        for line in process.stdout:
            line = line.strip()

            if "[download]" in line and "%" in line:
                match = re.search(r"(\d+\.?\d*)%", line)
                if match:
                    progress = float(match.group(1))
                    download_tasks[task_id]['progress'] = progress

            elif any(word in line for word in ["Merging", "Extracting", "Converting"]):
                download_tasks[task_id]['progress'] = 95

        process.wait()

        if process.returncode != 0:
            raise Exception("yt-dlp download failed")

        # --------------------------
        # FIND FINAL FILE
        # --------------------------
        download_dir = os.path.dirname(output_path)
        base_name = os.path.basename(output_path)

        found_files = [
            os.path.join(download_dir, f)
            for f in os.listdir(download_dir)
            if f.startswith(base_name)
        ]

        if not found_files:
            raise Exception("Downloaded file not found")

        final_output = max(found_files, key=os.path.getmtime)

        download_tasks[task_id]['status'] = 'completed'
        download_tasks[task_id]['progress'] = 100
        download_tasks[task_id]['file_path'] = final_output
        download_tasks[task_id]['file_ext'] = os.path.splitext(final_output)[1].lstrip(".")

    except Exception as e:
        print("Download error:", str(e))
        download_tasks[task_id]['status'] = 'failed'
        download_tasks[task_id]['error'] = str(e)


@app.route('/api/upload-cookies', methods=['POST'])
def upload_cookies():
    """Upload cookie file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if not file.filename.endswith('.txt'):
        return jsonify({'error': 'Only .txt files allowed'}), 400
    
    try:
        file.save(app.config['COOKIES_FILE'])
        return jsonify({'message': 'Cookie file uploaded successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/video/info', methods=['POST'])
def get_video_info():
    """Get video information"""
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    platform = get_platform(url)

    if not platform:
       return jsonify({'error': 'Only YouTube or Instagram URLs supported'}), 400

    
    try:
        # Clean up old files before processing
        cleanup_old_files()
        
        info = get_video_info_universal(url, platform)
        return jsonify(info), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/video/download', methods=['POST'])
def initiate_download():
    """Start video download"""
    data = request.get_json()
    url = data.get('url')
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    is_audio = data.get('is_audio', False)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    platform = get_platform(url)

    if not platform:
      return jsonify({'error': 'Only YouTube or Instagram supported'}), 400

    
    try:
        # Validate format_id for audio downloads
        if is_audio and not format_id:
            format_id = 'bestaudio/best'
        
        task_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{'audio' if is_audio else 'video'}_{timestamp}_{task_id[:8]}"
        output_path = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
        
        print(f"\n{'='*60}")
        print(f"New download request:")
        print(f"  Type: {'AUDIO (MP3)' if is_audio else 'VIDEO (MP4)'}")
        print(f"  Format: {format_id}")
        print(f"  Task ID: {task_id}")
        print(f"  Output: {output_path}")
        print(f"{'='*60}\n")
        
        download_tasks[task_id] = {
            'status': 'pending',
            'progress': 0,
            'url': url,
            'format_id': format_id,
            'is_audio': is_audio,
            'thumbnail_file': data.get('thumbnail_file'),
            'created_at': datetime.now().isoformat()
        }
        
        thread = threading.Thread(
            target=download_youtube_video,
            args=(url, format_id, task_id, output_path, is_audio)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'task_id': task_id,
            'message': 'Download started',
            'is_audio': is_audio,
        }), 202
        
    except Exception as e:
        print(f"Error initiating download: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/video/status/<task_id>', methods=['GET'])
def get_download_status(task_id):
    """Check download status"""
    if task_id not in download_tasks:
        return jsonify({'error': 'Task not found'}), 404
    
    task = download_tasks[task_id]
    
    return jsonify({
        'status': task.get('status'),
        'progress': task.get('progress', 0),
        'error': task.get('error')
    }), 200


@app.route('/api/video/file/<task_id>', methods=['GET'])
def download_file(task_id):

    # -------------------------
    # Validate Task
    # -------------------------
    if task_id not in download_tasks:
        return jsonify({'error': 'Task not found'}), 404

    task = download_tasks[task_id]

    if task.get('status') != 'completed':
        return jsonify({'error': 'Download not completed'}), 400

    file_path = task.get('file_path')

    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404

    # -------------------------
    # Determine MIME Type
    # -------------------------
    actual_ext = os.path.splitext(file_path)[1].lower().lstrip('.')

    mime_types = {
        'mp3': 'audio/mpeg',
        'mp4': 'video/mp4',
        'm4a': 'audio/mp4',
        'webm': 'video/webm',
        'mkv': 'video/x-matroska',
    }

    mimetype = mime_types.get(actual_ext, 'application/octet-stream')

    print("\nSending file to client:")
    print(f"  Path: {file_path}")
    print(f"  MIME: {mimetype}")

    # -------------------------
    # DELAYED CLEANUP THREAD
    # -------------------------
    def delayed_cleanup(path, task_id):
        time.sleep(5)  # allow file stream to finish

        try:
            task = download_tasks.get(task_id)

            # Delete video/audio file
            if os.path.exists(path):
                os.remove(path)
                print(f"✓ Deleted file: {path}")

            # Delete thumbnail file (SAFE METHOD)
            if task:
                thumb_file = task.get('thumbnail_file')

                if thumb_file:
                    thumb_path = os.path.join(app.config['DOWNLOAD_FOLDER'], thumb_file)

                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)
                        print(f"✓ Deleted thumbnail: {thumb_path}")

            # Remove task
            if task_id in download_tasks:
                del download_tasks[task_id]
                print(f"✓ Removed task: {task_id}")

        except Exception as e:
            print(f"Cleanup error: {str(e)}")

    threading.Thread(
        target=delayed_cleanup,
        args=(file_path, task_id),
        daemon=True
    ).start()

    # -------------------------
    # Send File
    # -------------------------
    return send_file(
        file_path,
        as_attachment=True,
        download_name=os.path.basename(file_path),
        mimetype=mimetype
    )


@app.route('/api/thumbnail/<filename>', methods=['GET'])
def serve_thumbnail(filename):
    """Serve downloaded thumbnail"""
    try:
        filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Thumbnail not found'}), 404
        
        return send_file(filepath, mimetype='image/jpeg')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'cookie_exists': os.path.exists(app.config['COOKIES_FILE']),
        'total_tasks': len(download_tasks),
        'download_folder': app.config['DOWNLOAD_FOLDER']
    }), 200


if __name__ == "__main__":
    print("=" * 60)
    print("Video Download API - Enhanced Version")
    print("=" * 60)
    print(f"Cookie file: {app.config['COOKIES_FILE']}")
    print(f"Download folder: {app.config['DOWNLOAD_FOLDER']}")
    print("=" * 60)

    cookie_status = "✓ Found" if os.path.exists(app.config['COOKIES_FILE']) else "✗ Not found"
    print(f"Cookie file status: {cookie_status}")

    # Optional diagnostics (safe for production)
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
        print(f"yt-dlp version: {result.stdout.strip()}")
    except:
        print("WARNING: yt-dlp not found!")

    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        print(f"ffmpeg: {result.stdout.splitlines()[0]}")
    except:
        print("WARNING: ffmpeg not found!")

    print("=" * 60)

    # Use environment port (required for Render)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

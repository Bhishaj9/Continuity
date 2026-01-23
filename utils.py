import os
import shutil
import requests
import tempfile
import logging
import json
import subprocess
from datetime import datetime, timedelta
from google.cloud import storage
from config import Settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_to_temp(url):
    """Downloads a file from a URL to a temporary local file."""
    logger.info(f"Downloading: {url}")
    if os.path.exists(url):
        return url
        
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    suffix = os.path.splitext(url.split("/")[-1])[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        shutil.copyfileobj(resp.raw, f)
        return f.name

def download_blob(gcs_uri, destination_file_name):
    """Downloads a blob from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
        
    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(destination_file_name)
    logger.info(f"Downloaded storage object {gcs_uri} to local file {destination_file_name}.")

def upload_to_gcs(local_path, destination_blob_name):
    """Uploads a file to GCS and returns a signed URL."""
    if not Settings.GCP_BUCKET_NAME:
        return None
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(Settings.GCP_BUCKET_NAME)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        
        url = blob.generate_signed_url(expiration=timedelta(hours=1), method='GET')
        logger.info(f"Uploaded {local_path} to {destination_blob_name}. URL: {url}")
        return url
    except Exception as e:
        logger.error(f"GCS Upload Failed: {e}")
        return None

def get_history_from_gcs():
    """Lists recent videos from GCS."""
    if not Settings.GCP_BUCKET_NAME:
        return []
        
    try:
        storage_client = storage.Client()
        blobs = list(storage_client.bucket(Settings.GCP_BUCKET_NAME).list_blobs())
        # Sort by time created (newest first)
        blobs.sort(key=lambda b: b.time_created, reverse=True)
        
        history = []
        for b in blobs[:20]:
            if b.name.endswith(".mp4"):
                history.append({
                    "name": b.name,
                    "url": b.generate_signed_url(timedelta(hours=1), method='GET'),
                    "created": b.time_created.isoformat()
                })
        return history
    except Exception as e:
        logger.error(f"Failed to list GCS history: {e}")
        return []

def save_video_bytes(bytes_data, suffix=".mp4") -> str:
    """Saves raw video bytes to a temporary local file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(bytes_data)
        return f.name

def stitch_videos(path_a, path_b, path_c, output_path):
    """ 
    Smart Stitching: Normalizes A, B, and C to 1080p/24fps and concatenates them. 
    Handles different resolutions/codecs gracefully using FFmpeg filters. 
    """
    logger.info("🧵 Stitching: A=%s + B=%s + C=%s -> %s", path_a, path_b, path_c, output_path)

    # Filter: Scale to 1920x1080 (force), set SAR 1:1, set fps 24, concat.
    cmd = [
        "ffmpeg", "-y",
        "-i", path_a,
        "-i", path_b,
        "-i", path_c,
        "-filter_complex",
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v0];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v1];"
        "[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v2];"
        "[v0][0:a][v1][1:a][v2][2:a]concat=n=3:v=1:a=1[v][a]",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]

    # Fallback command if audio is missing in A or C (common issue)
    # This generates silent audio for inputs that lack it (by ignoring audio in concat and outputting video only, or we could generate silence but this prompt used a video-only fallback rationale or just stripped audio)
    # The prompt's fallback command: "[v0][v1][v2]concat=n=3:v=1:a=0[v]" -> video only.
    cmd_robust = [
        "ffmpeg", "-y",
        "-i", path_a,
        "-i", path_b,
        "-i", path_c,
        "-filter_complex",
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v0];"
        f"[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v1];"
        f"[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v2];"
        f"[v0][v1][v2]concat=n=3:v=1:a=0[v]",
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "fast",
        output_path
    ]

    try:
        # Try full stitch with audio
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.warning(f"Audio stitch failed (likely missing audio tracks), retrying video-only stitch... Error: {e}")
        try:
            subprocess.run(cmd_robust, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e2:
            logger.error(f"Critical Stitching Error: {e2.stderr.decode()}")
            raise e2
    return output_path

def update_job_status(job_id, status, progress, log=None, video_url=None):
    """Updates JSON status."""
    if not job_id:
        return
        
    os.makedirs("outputs", exist_ok=True)

    final_url = video_url
    if video_url and os.path.exists(video_url) and status == "completed":
        filename = os.path.basename(video_url)
        # Avoid double-renaming if we are already dealing with a final name or just use unique
        final_filename = f"{job_id}_final_{filename}" if "final" not in filename else filename
        dest = os.path.join("outputs", final_filename)
        
        # Move only if source != dest
        if os.path.abspath(video_url) != os.path.abspath(dest):
             shutil.move(video_url, dest)
             
        final_url = f"/outputs/{final_filename}"
        
        # Auto backup for final result
        if Settings.GCP_BUCKET_NAME:
            upload_to_gcs(dest, final_filename)

    file_path = os.path.join("outputs", f"{job_id}.json")
    with open(file_path, "w") as f:
        json.dump({
            "status": status,
            "progress": progress,
            "log": log,
            "video_url": final_url
        }, f)

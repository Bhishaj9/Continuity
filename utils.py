import os
import shutil
import requests
import tempfile
import logging
import json
import subprocess
from datetime import timedelta
from google.cloud import storage
from config import Settings
logger = logging.getLogger(__name__)

def download_to_temp(url):
    if os.path.exists(url): return url
    resp = requests.get(url, stream=True); resp.raise_for_status()
    suffix = os.path.splitext(url.split("/")[-1])[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        shutil.copyfileobj(resp.raw, f)
    return f.name

def download_blob(gcs_uri, destination_file_name):
    if not gcs_uri.startswith("gs://"): raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    parts = gcs_uri[5:].split("/", 1)
    storage.Client().bucket(parts[0]).blob(parts[1]).download_to_filename(destination_file_name)

def upload_to_gcs(local_path, destination_blob_name):
    if not Settings.GCP_BUCKET_NAME: return None
    try:
        blob = storage.Client().bucket(Settings.GCP_BUCKET_NAME).blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        return blob.generate_signed_url(expiration=timedelta(hours=1), method='GET')
    except Exception as e:
        logger.error(f"GCS Upload Failed: {e}")
        return None

def get_history_from_gcs():
    if not Settings.GCP_BUCKET_NAME: return []
    try:
        blobs = list(storage.Client().bucket(Settings.GCP_BUCKET_NAME).list_blobs())
        blobs.sort(key=lambda b: b.time_created, reverse=True)
        return [{"name": b.name, "url": b.generate_signed_url(timedelta(hours=1), method='GET'), "created": b.time_created.isoformat()} for b in blobs[:20] if b.name.endswith(".mp4")]
    except Exception:
        return []

def save_video_bytes(bytes_data, suffix=".mp4") -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(bytes_data)
    return f.name

def normalize_video(input_path):
    """Converts any video to a standard 1080p, 24fps, silent MP4 intermediate."""
    output_path = input_path.replace(".mp4", "_norm.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-an", # Remove audio to prevent mixing crashes
        output_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    return output_path

def stitch_videos(path_a, path_b, path_c, output_path):
    """ Robust Stitching: Normalizes clips individually first, then concats. This avoids the 'complex filter' crashes common with mismatched inputs. """
    logger.info(f"🧵 Stitching: {path_a} + {path_b} + {path_c}")

    try:
        # 1. Normalize all inputs to identical format
        norm_a = normalize_video(path_a)
        norm_b = normalize_video(path_b)
        norm_c = normalize_video(path_c)
        
        # 2. Create list file for concat
        list_file = "concat_list.txt"
        with open(list_file, "w") as f:
            f.write(f"file '{norm_a}'\n")
            f.write(f"file '{norm_b}'\n")
            f.write(f"file '{norm_c}'\n")
        
        # 3. Concatenate using stream copy (fast & safe)
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", output_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        
        # Cleanup temps
        for p in [norm_a, norm_b, norm_c, list_file]:
            if os.path.exists(p): os.remove(p)
            
        return output_path
        
    except Exception as e:
        logger.error(f"Stitch Logic Failed: {e}")
        raise e

def update_job_status(job_id, status, progress, log=None, video_url=None, merged_video_url=None):
    if not job_id: return
    os.makedirs("outputs", exist_ok=True)

    final_url = video_url
    final_merged_url = merged_video_url
    # Move Bridge
    if video_url and os.path.exists(video_url) and status == "completed":
        final_filename = f"{job_id}_bridge.mp4"
        dest = os.path.join("outputs", final_filename)
        if os.path.abspath(video_url) != os.path.abspath(dest): shutil.move(video_url, dest)
        final_url = f"/outputs/{final_filename}"
        if Settings.GCP_BUCKET_NAME: upload_to_gcs(dest, final_filename)
    # Move Merged
    if merged_video_url and os.path.exists(merged_video_url) and status == "completed":
        merged_filename = f"{job_id}_merged.mp4"
        merged_dest = os.path.join("outputs", merged_filename)
        if os.path.abspath(merged_video_url) != os.path.abspath(merged_dest): shutil.move(merged_video_url, merged_dest)
        final_merged_url = f"/outputs/{merged_filename}"
    with open(f"outputs/{job_id}.json", "w") as f:
        json.dump({
            "status": status, "progress": progress, "log": log,
            "video_url": final_url, "merged_video_url": final_merged_url
        }, f)

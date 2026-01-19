import os
import shutil
import requests
import tempfile
import logging
import json
from google.cloud import storage

# Configure logging for utils
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
    """Downloads a blob from the Google Cloud Storage bucket."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    source_blob_name = parts[1]
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)
    logger.info(f"Downloaded storage object {gcs_uri} to local file {destination_file_name}.")

def save_video_bytes(bytes_data, suffix=".mp4") -> str:
    """Saves raw video bytes to a temporary local file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(bytes_data)
        local_path = f.name
    logger.info(f"✅ Video bytes saved to: {local_path}")
    return local_path

def update_job_status(job_id, status, progress, log=None, video_url=None):
    """Writes a JSON file to outputs/{job_id}.json with current job status."""
    if not job_id:
        return

    output_dir = "outputs"
    os.makedirs(output_dir, exist_ok=True)
    
    # Handle video file move if completed
    final_video_url = video_url
    if video_url and os.path.exists(video_url) and status == "completed":
        try:
            filename = os.path.basename(video_url)
            # Ensure unique name or use job_id
            final_filename = f"{job_id}_final{os.path.splitext(filename)[1]}"
            destination = os.path.join(output_dir, final_filename)
            shutil.move(video_url, destination)
            logger.info(f"Moved video to {destination}")
            # Set public URL relative to server root
            final_video_url = f"/outputs/{final_filename}"
        except Exception as e:
            logger.error(f"Failed to move output video: {e}")

    file_path = os.path.join(output_dir, f"{job_id}.json")
    
    data = {
        "status": status,
        "progress": progress,
        "log": log,
        "video_url": final_video_url
    }
    
    try:
        with open(file_path, "w") as f:
            json.dump(data, f)
        logger.info(f"Job {job_id} updated: {status} ({progress}%) - {log}")
    except Exception as e:
        logger.error(f"Failed to update job status for {job_id}: {e}")

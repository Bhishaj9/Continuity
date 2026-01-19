import os
import shutil
import requests
import tempfile
import logging
import json
from datetime import datetime, timedelta
from google.cloud import storage
from config import Settings

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

def upload_to_gcs(local_path, destination_blob_name):
    """Uploads a file to the bucket."""
    bucket_name = Settings.GCP_BUCKET_NAME
    if not bucket_name:
        logger.warning("GCP_BUCKET_NAME not set. Skipping upload.")
        return None

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        blob.upload_from_filename(local_path)
        
        # Generate signed URL (valid for 1 hour)
        url = blob.generate_signed_url(expiration=timedelta(hours=1), method='GET')
        logger.info(f"Uploaded {local_path} to {destination_blob_name}. URL: {url}")
        return url
    except Exception as e:
        logger.error(f"Failed to upload to GCS: {e}")
        return None

def get_history_from_gcs():
    """Lists recent videos from GCS."""
    bucket_name = Settings.GCP_BUCKET_NAME
    if not bucket_name:
        return []

    try:
        storage_client = storage.Client()
        blobs = storage_client.list_blobs(bucket_name)
        
        # Sort by time created (newest first)
        sorted_blobs = sorted(blobs, key=lambda b: b.time_created, reverse=True)
        
        history = []
        for blob in sorted_blobs[:20]: # Limit to 20
             if blob.name.endswith(".mp4"):
                 url = blob.generate_signed_url(expiration=timedelta(hours=1), method='GET')
                 history.append({
                     "name": blob.name,
                     "url": url,
                     "created": blob.time_created.isoformat()
                 })
        return history
    except Exception as e:
        logger.error(f"Failed to list GCS history: {e}")
        return []

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
            
            # --- AUTO BACKUP TO CLOUD ---
            if Settings.GCP_BUCKET_NAME:
                logger.info(f"Backing up {final_filename} to GCS...")
                upload_to_gcs(destination, final_filename)
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

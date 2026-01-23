import os, shutil, requests, tempfile, logging, json, subprocess
from datetime import timedelta
from google.cloud import storage
from config import Settings
logger = logging.getLogger(__name__)

def download_to_temp(url):
    if os.path.exists(url): return url
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
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

def stitch_videos(path_a, path_b, path_c, output_path):
    logger.info("🧵 Stitching: A=%s + B=%s + C=%s -> %s", path_a, path_b, path_c, output_path)
    cmd = [
        "ffmpeg", "-y", "-i", path_a, "-i", path_b, "-i", path_c,
        "-filter_complex",
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v0];[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v1];[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v2];[v0][0:a][v1][1:a][v2][2:a]concat=n=3:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "192k", output_path
    ]
    cmd_robust = [
        "ffmpeg", "-y", "-i", path_a, "-i", path_b, "-i", path_c,
        "-filter_complex",
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v0];[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v1];[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v2];[v0][v1][v2]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "fast", output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(cmd_robust, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e2:
            raise e2
    return output_path

def update_job_status(job_id, status, progress, log=None, video_url=None, merged_video_url=None):
    if not job_id: return
    os.makedirs("outputs", exist_ok=True)
    final_url = video_url
    final_merged_url = merged_video_url
    
    if video_url and os.path.exists(video_url) and status == "completed":
        final_filename = f"{job_id}_bridge.mp4"
        dest = os.path.join("outputs", final_filename)
        if os.path.abspath(video_url) != os.path.abspath(dest):
            shutil.move(video_url, dest)
        final_url = f"/outputs/{final_filename}"
        if Settings.GCP_BUCKET_NAME:
            upload_to_gcs(dest, final_filename)
            
    if merged_video_url and os.path.exists(merged_video_url) and status == "completed":
        merged_filename = f"{job_id}_merged.mp4"
        merged_dest = os.path.join("outputs", merged_filename)
        if os.path.abspath(merged_video_url) != os.path.abspath(merged_dest):
            shutil.move(merged_video_url, merged_dest)
        final_merged_url = f"/outputs/{merged_filename}"
        
    with open(f"outputs/{job_id}.json", "w") as f:
        json.dump({"status": status, "progress": progress, "log": log, "video_url": final_url, "merged_video_url": final_merged_url}, f)

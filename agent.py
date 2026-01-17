import os
import time
import shutil
import requests
import tempfile
import logging
import json
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
# Unified SDK for both Analyst (Gemini) and Generator (Veo)
from google import genai
from google.genai import types
from google.cloud import storage # Required for downloading Veo output

from groq import Groq
from gradio_client import Client, handle_file
from dotenv import load_dotenv

# --- AUTH SETUP FOR HUGGING FACE ---
if "GCP_CREDENTIALS_JSON" in os.environ:
    # logger is not defined yet, using print
    print("🔐 Found GCP Credentials Secret. Setting up auth...")
    creds_path = "gcp_credentials.json"
    with open(creds_path, "w") as f:
        f.write(os.environ["GCP_CREDENTIALS_JSON"])
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State Definition
class ContinuityState(TypedDict):
    video_a_url: str
    video_c_url: str
    user_notes: Optional[str]
    scene_analysis: Optional[str]
    veo_prompt: Optional[str]
    generated_video_url: Optional[str]
    video_a_local_path: Optional[str]
    video_c_local_path: Optional[str]

# --- HELPER FUNCTIONS ---
def download_to_temp(url):
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
    """Downloads a blob from the bucket."""
    # gcs_uri format: gs://bucket-name/path/to/object
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

# --- NODE 1: ANALYST ---
def analyze_videos(state: ContinuityState) -> dict:
    logger.info("--- 🧐 Analyst Node (Director) ---")

    video_a_url = state['video_a_url']
    video_c_url = state['video_c_url']

    # 1. Prepare Files
    try:
        path_a = state.get('video_a_local_path')
        if not path_a:
            path_a = download_to_temp(video_a_url)

        path_c = state.get('video_c_local_path')
        if not path_c:
            path_c = download_to_temp(video_c_url)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return {"scene_analysis": "Error downloading", "veo_prompt": "Smooth cinematic transition"}

    # 2. Try Gemini 2.0 (With Retry)
    # Standard Client for Gemini (API Key based)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    transition_prompt = None
    retries = 3
    for attempt in range(retries):
        try:
            logger.info(f"Uploading videos to Gemini... (Attempt {attempt+1})")
            file_a = client.files.upload(file=path_a)
            file_c = client.files.upload(file=path_c)

            prompt_text = """
            You are a film director. 
            Analyze the motion, lighting, and subject of the first video (Video A) and the second video (Video C). 
            Write a detailed visual prompt for a 2-second video (Video B) that smoothly transitions from the end of A to the start of C.
            Target Output: A single concise descriptive paragraph for the video generation model.
            """

            logger.info("Generating transition prompt...")
            # Using 2.0 Flash Exp or falling back to 1.5 Flash if needed
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp", 
                contents=[prompt_text, file_a, file_c]
            )
            transition_prompt = response.text
            logger.info(f"Generated Prompt: {transition_prompt}")
            break # Success
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 30 * (attempt + 1)
                logger.warning(f"⚠️ Gemini Quota 429. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"⚠️ Gemini Error: {e}")
                break

    # 3. Fallback: Groq (If Gemini failed)
    if not transition_prompt:
        logger.info("Switching to Llama 3.2 (Groq) Fallback...")
        try:
            groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
            fallback_prompt = "Create a smooth, cinematic visual transition that bridges two scenes."
            completion = groq_client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{"role": "user", "content": f"Refine this into a video prompt: {fallback_prompt}"}]
            )
            transition_prompt = completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ Groq also failed: {e}")
            transition_prompt = "Smooth cinematic transition with motion blur matching the scenes."

    return {
        "scene_analysis": transition_prompt, 
        "veo_prompt": transition_prompt,
        "video_a_local_path": path_a,
        "video_c_local_path": path_c
    }

# --- NODE 2: GENERATOR ---
def generate_video(state: ContinuityState) -> dict:
    logger.info("--- 🎥 Generator Node ---")

    prompt = state.get('veo_prompt', "")
    path_a = state.get('video_a_local_path')
    path_c = state.get('video_c_local_path')
    if not path_a or not path_c:
        return {"generated_video_url": "Error: Missing local video paths"}

    # --- ATTEMPT 1: GOOGLE VEO (VIA UNIFIED GENAI SDK) ---
    try:
        logger.info("⚡ Initializing Google Veo (Unified SDK)...")
        project_id = os.getenv("GCP_PROJECT_ID")
        location = os.getenv("GCP_LOCATION", "us-central1")
        
        if project_id:
            # Initialize Vertex AI Client via genai
            client = genai.Client(
                vertexai=True, 
                project=project_id, 
                location=location
            )
            
            logger.info(f"Generating with Veo... Prompt: {prompt[:30]}...")
            
            # Submit Generation Operation
            operation = client.models.generate_videos(
                model='veo-2.0-generate-001',
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                )
            )
            
            # Polling Loop
            logger.info(f"Waiting for Veo operation {operation.name}...")
            while not operation.done:
                time.sleep(10)
                operation = client.operations.get(operation.name)
                logger.info("...still generating...")
            
            # Handle Result
            if operation.result and operation.result.generated_videos:
                video_result = operation.result.generated_videos[0]
                
                # Check if we have a GCS URI (Typical for Veo)
                if hasattr(video_result.video, 'uri') and video_result.video.uri:
                    gcs_uri = video_result.video.uri
                    logger.info(f"Veo output saved to GCS: {gcs_uri}")
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                        local_path = f.name
                    
                    download_blob(gcs_uri, local_path)
                    logger.info(f"✅ Veo Video Downloaded: {local_path}")
                    return {"generated_video_url": local_path}
                else:
                    logger.warning("Veo operation completed but no URI found.")
            else:
                logger.warning("Veo operation completed with no result.")

        else:
            logger.warning("⚠️ GCP_PROJECT_ID not set. Skipping Veo.")
            
    except Exception as e:
        logger.warning(f"⚠️ Veo Failed: {e}")
        # Fallback to SVD below

    # --- ATTEMPT 2: SVD FALLBACK (Free) ---
    logger.info("🔄 Switching to SVD Fallback...")
    try:
        import cv2
        from PIL import Image
        
        def get_frame(video_path):
            cap = cv2.VideoCapture(video_path)
            ret, frame = cap.read()
            cap.release()
            if ret:
                return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            return None

        img_start = get_frame(path_a)
        if img_start is None:
             raise ValueError("Could not read start frame for SVD")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_start:
            img_start.save(f_start, format="PNG")
            start_path = f_start.name
            
        client = Client("multimodalart/stable-video-diffusion")
        result = client.predict(
            handle_file(start_path),
            0.0, 0.0, 1, 25,
            api_name="/predict"
        )
        logger.info(f"✅ SVD Generated: {result}")
        return {"generated_video_url": result}
        
    except Exception as e:
        logger.error(f"❌ All Generators Failed. Error: {e}")
        return {"generated_video_url": f"Error: {str(e)}"}

# Graph Construction
workflow = StateGraph(ContinuityState)
workflow.add_node("analyst", analyze_videos)
workflow.add_node("generator", generate_video)
workflow.set_entry_point("analyst")
workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)
app = workflow.compile()

# --- SERVER COMPATIBILITY WRAPPERS ---
def analyze_only(state_or_path_a, path_c=None):
    # Handle direct server call format (path_a, path_c)
    if isinstance(state_or_path_a, str) and path_c:
        state = {
            "video_a_url": "local",
            "video_c_url": "local",
            "video_a_local_path": state_or_path_a,
            "video_c_local_path": path_c
        }
    else:
        state = state_or_path_a if isinstance(state_or_path_a, dict) else state_or_path_a.dict()

    result = analyze_videos(state)
    return {"prompt": result.get("scene_analysis"), "status": "success"}

def generate_only(prompt, path_a, path_c):
    state = {
        "video_a_url": "local",
        "video_c_url": "local",
        "video_a_local_path": path_a,
        "video_c_local_path": path_c,
        "veo_prompt": prompt
    }
    return generate_video(state)

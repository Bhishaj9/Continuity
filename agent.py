import os
import time
import shutil
import requests
import tempfile
import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from google import genai
from groq import Groq
from gradio_client import Client, handle_file
from dotenv import load_dotenv

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
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    suffix = os.path.splitext(url.split("/")[-1])[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        shutil.copyfileobj(resp.raw, f)
        return f.name

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
            # Using 2.0 Flash as per your logs (or 1.5-flash if preferred)
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
            # We can't easily send videos, so we generate a prompt based on general best practices
            fallback_prompt = "Create a smooth, cinematic visual transition that bridges two scenes with matching lighting and motion blur."
            
            completion = groq_client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[
                    {"role": "user", "content": f"Refine this into a video generation prompt: {fallback_prompt}"}
                ]
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

    try:
        # Extract Frames (simplified for brevity, ensuring libraries are imported)
        import cv2
        from PIL import Image
        
        def get_frame(video_path, location="last"):
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if location == "last": cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
            else: cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            cap.release()
            if ret: return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            raise ValueError(f"Could not extract frame from {video_path}")

        logger.info("Extracting frames...")
        img_start = get_frame(path_a, "last")
        img_end = get_frame(path_c, "first")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_start:
            img_start.save(f_start, format="PNG")
            start_path = f_start.name
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_end:
            img_end.save(f_end, format="PNG")
            end_path = f_end.name

        # --- ATTEMPT 1: WAN 2.2 ---
        try:
            logger.info("Initializing Wan Client...")
            client = Client("multimodalart/wan-2-2-first-last-frame")
            
            logger.info(f"Generating with Wan 2.2... Prompt: {prompt[:30]}...")
            result = client.predict(
                start_image_pil=handle_file(start_path),
                end_image_pil=handle_file(end_path),
                prompt=prompt,
                negative_prompt="blurry, distorted, low quality, static",
                duration_seconds=2.1,
                steps=20,
                guidance_scale=5.0,
                guidance_scale_2=5.0,
                seed=42,
                randomize_seed=True,
                api_name="/generate_video"
            )
            # Handle Wan output format
            video_out = result[0]
            if isinstance(video_out, dict) and 'video' in video_out:
                 return {"generated_video_url": video_out['video']}
            elif isinstance(video_out, str) and os.path.exists(video_out):
                 return {"generated_video_url": video_out}
                 
        except Exception as e:
            logger.warning(f"⚠️ Wan 2.2 Failed: {e}")

        # --- ATTEMPT 2: SVD FALLBACK ---
        logger.info("🔄 Switching to SVD Fallback...")
        try:
            # FIXED REPO ID
            client = Client("multimodalart/stable-video-diffusion")
            
            # SVD uses one image, we'll use the start frame
            result = client.predict(
                handle_file(start_path),
                0.0, 0.0, 1, 25, # resized_width, resized_height, motion_bucket_id, fps
                api_name="/predict"
            )
            logger.info(f"✅ SVD Generated: {result}")
            return {"generated_video_url": result} # SVD usually returns path string
            
        except Exception as e:
            logger.error(f"❌ All Generators Failed. Error: {e}")
            return {"generated_video_url": f"Error: {str(e)}"}

    except Exception as e:
        logger.error(f"Error in Generator Setup: {e}")
        return {"generated_video_url": f"Error: {str(e)}"}


# Graph Construction
workflow = StateGraph(ContinuityState)
workflow.add_node("analyst", analyze_videos)
workflow.add_node("generator", generate_video)
workflow.set_entry_point("analyst")
workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)
app = workflow.compile()
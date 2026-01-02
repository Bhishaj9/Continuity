import os
import time

from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from google import genai
from gradio_client import Client, handle_file
import shutil
import requests
import tempfile
import os
import shutil
import shutil
import requests
import tempfile
import base64
import numpy as np
import cv2
from groq import Groq
from PIL import Image

from dotenv import load_dotenv

load_dotenv()

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

def create_filmstrip(video_path, samples=5, is_start=False):
    """
    Captures 'samples' frames from the first 2s (is_start=True) or last 2s (is_start=False).
    Stitches them horizontally into a single filmstrip image.
    Returns the file path of the saved filmstrip.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    
    # Define time range
    if is_start:
        start_sec = 0
        end_sec = min(2, duration)
    else:
        start_sec = max(0, duration - 2)
        end_sec = duration

    target_times = np.linspace(start_sec, end_sec, samples)
    frames = []

    for t in target_times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if ret:
            # Resize logic: fixed height 300px, maintain aspect ratio
            h, w = frame.shape[:2]
            new_h = 300
            scale = new_h / h
            new_w = int(w * scale)
            frame_resized = cv2.resize(frame, (new_w, new_h))
            frames.append(frame_resized)
    
    cap.release()

    if not frames:
        return None

    # Stitch horizontally
    filmstrip = np.hstack(frames)
    
    # Save to temp file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    cv2.imwrite(temp_file.name, filmstrip)
    return temp_file.name

# Node 1: Analyst
def analyze_videos(state: ContinuityState) -> dict:
    print("--- Analyst Node (Director: Dual-Engine) ---")
    
    video_a_url = state['video_a_url']
    video_c_url = state['video_c_url']
    
    # Initialize Google GenAI Client
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    
    try:
        # Download videos to temp files for analysis
        def download_to_temp(url):
            print(f"Downloading: {url}")
            resp = requests.get(url, stream=True)
            resp.raise_for_status()
            suffix = os.path.splitext(url.split("/")[-1])[1] or ".mp4"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                shutil.copyfileobj(resp.raw, f)
                return f.name

        path_a = state.get('video_a_local_path')
        if not path_a:
             path_a = download_to_temp(video_a_url)
             
        path_c = state.get('video_c_local_path')
        if not path_c:
             path_c = download_to_temp(video_c_url)
        
        # --- Create Filmstrips ---
        print("Creating filmstrips for visual analysis...")
        filmstrip_a_path = create_filmstrip(path_a, is_start=False) # End of A
        filmstrip_c_path = create_filmstrip(path_c, is_start=True)  # Start of C

        if not filmstrip_a_path or not filmstrip_c_path:
             print("Warning: Could not create filmstrips. Using fallback prompt.")
             return {
                "scene_analysis": "Error creating filmstrips.", 
                "veo_prompt": "Cinematic transition, high quality, 4k.",
                "video_a_local_path": path_a,
                "video_c_local_path": path_c
             }

        # --- Primary Engine: Gemini 2.0 Flash ---
        print("Engaging Primary Engine: Gemini 2.0 Flash...")
        try:
             file_a = client.files.upload(file=filmstrip_a_path)
             file_c = client.files.upload(file=filmstrip_c_path)

             system_prompt = """
             You are an expert film editor. Analyze these two 'filmstrips'. 
             Image 1 shows the end of the first clip (time flows left-to-right). 
             Image 2 shows the start of the next clip. 
             Describe the motion, lighting, and subject connection required to seamlessly bridge A to C in a cinematic way.
             Output a SINGLE concise paragraph for a video generation model.
             """
             
             response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[system_prompt, file_a, file_c]
             )
             
             transition_prompt = response.text
             print(f"Gemini Generated Prompt: {transition_prompt}")
             
             # Cleanup uploaded files? (Optional, but good practice if high volume)
             
             final_prompt = transition_prompt

        except Exception as e:
            if "429" in str(e) or "Resource Exhausted" in str(e):
                print(f"⚠️ Gemini Quota hit ({str(e)}). Switching to Fallback Engine...")
                
                # --- Fallback Engine: Groq Llama 3.2 Vision ---
                print("Engaging Fallback Engine: Llama 3.2 Vision (Groq)...")
                groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
                
                def encode_image(image_path):
                    with open(image_path, "rb") as image_file:
                        return base64.b64encode(image_file.read()).decode('utf-8')
                
                base64_a = encode_image(filmstrip_a_path)
                base64_c = encode_image(filmstrip_c_path)
                
                completion = groq_client.chat.completions.create(
                    model="llama-3.2-11b-vision-preview",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "You are an expert film editor. Analyze these two 'filmstrips'. Image 1 shows the end of the first clip. Image 2 shows the start of the next clip. Describe the motion, lighting, and subject connection required to seamlessly bridge A to C in a cinematic way. Output a SINGLE concise paragraph for a video generation model."
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{base64_a}"
                                    }
                                },
                                {
                                     "type": "image_url",
                                     "image_url": {
                                         "url": f"data:image/jpeg;base64,{base64_c}"
                                     }
                                }
                            ]
                        }
                    ],
                    temperature=0.7,
                    max_tokens=500,
                    top_p=1,
                    stream=False,
                    stop=None,
                )
                
                final_prompt = completion.choices[0].message.content
                print(f"Groq Generated Prompt: {final_prompt}")
            
            else:
                print(f"Error in Primary Engine: {e}")
                raise e

        # Cleanup local filmstrips
        try:
            os.remove(filmstrip_a_path)
            os.remove(filmstrip_c_path)
        except:
             pass

        return {
            "scene_analysis": final_prompt, 
            "veo_prompt": final_prompt,
            "video_a_local_path": path_a,
            "video_c_local_path": path_c
        }

    except Exception as e:
        print(f"Error in Analyst: {e}")
        return {"scene_analysis": f"Error: {str(e)}", "veo_prompt": "Error"}


# Node 2: Generator (Wan 2.2 First Last Frame)
def generate_video(state: ContinuityState) -> dict:
    print("--- Generator Node (Wan 2.2) ---")
    
    prompt = state.get('veo_prompt', "")
    path_a = state.get('video_a_local_path')
    path_c = state.get('video_c_local_path')
    
    if not path_a or not path_c:
        # Fallback if dependencies failed or state clean
        # Re-download logic would go here, but assuming flow works
        return {"generated_video_url": "Error: Missing local video paths"}

    try:
        # Extract Frames
        import cv2
        from PIL import Image
        
        def get_frame(video_path, location="last"):
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if location == "last":
                cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
            else: # first
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                # Resize to a safe resolution (height=480) to prevent server crash
                height, width = frame.shape[:2]
                if height > 480:
                    scale = 480 / height
                    new_width = int(width * scale)
                    frame = cv2.resize(frame, (new_width, 480))

                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(frame_rgb)
            else:
                raise ValueError(f"Could not extract frame from {video_path}")

        print("Extracting frames...")
        img_start = get_frame(path_a, "last")
        img_end = get_frame(path_c, "first")
        
        # Save frames to temp files for Gradio Client (it handles file paths better than PIL objects usually)
        # Although client.predict might take PIL, handle_file is safer with paths.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_start:
            img_start.save(f_start, format="PNG")
            start_path = f_start.name
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_end:
            img_end.save(f_end, format="PNG")
            end_path = f_end.name

        # Call Wan 2.2
        print("Initializing Wan Client...")
        client = Client("multimodalart/wan-2-2-first-last-frame", token=os.environ.get("HF_TOKEN"))
        
        result = None
        for i in range(3):
            try:
                print(f"Generating transition with prompt: {prompt[:50]}... (Attempt {i+1})")
                # predict(start_image, end_image, prompt, negative_prompt, duration, steps, guide, guide2, seed, rand, api_name)
                result = client.predict(
                    start_image_pil=handle_file(start_path),
                    end_image_pil=handle_file(end_path),
                    prompt=prompt,
                    negative_prompt="blurry, distorted, low quality, static",
                    duration_seconds=2.1,
                    steps=20, # Default is often around 20-30 for good quality
                    guidance_scale=5.0,
                    guidance_scale_2=5.0,
                    seed=42,
                    randomize_seed=True,
                    api_name="/generate_video"
                )
                break
            except Exception as e:
                print(f"⚠️ Attempt {i+1} failed: {e}. Retrying in 10s...")
                time.sleep(10)

        if result is None:
            return {"generated_video_url": "Error: Generator failed after 3 retries."}
        
        # Clean up temp frames and videos
        try:
            os.remove(start_path)
            os.remove(end_path)
            os.remove(path_a)
            os.remove(path_c)
        except:
            pass

        # Parse valid result
        # Expected: ({'video': path, ...}, seed) or just path depending on version
        # Based on inspection: (generated_video_mp4, seed)
        video_out = result[0]
        if isinstance(video_out, dict) and 'video' in video_out:
             return {"generated_video_url": video_out['video']}
        elif isinstance(video_out, str) and os.path.exists(video_out):
             return {"generated_video_url": video_out}
        else:
             return {"generated_video_url": f"Error: Unexpected output {result}"}

    except Exception as e:
        print(f"Error in Generator: {e}")
        return {"generated_video_url": f"Error: {str(e)}"}


# Graph Construction
workflow = StateGraph(ContinuityState)

workflow.add_node("analyst", analyze_videos)
# workflow.add_node("prompter", draft_prompt) # Skipped, Analyst does extraction + prompting
workflow.add_node("generator", generate_video)

workflow.set_entry_point("analyst")

workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)

app = workflow.compile()

import os
import time
import shutil
import cv2
import numpy as np
import base64
import tempfile
from groq import Groq
from google import genai
from gradio_client import Client, handle_file
from dotenv import load_dotenv

load_dotenv()

# --- HELPER: Filmstrip Engine ---
def create_filmstrip(video_path, samples=5, is_start=False):
    """Extracts frames and stitches them into a filmstrip for Vision analysis."""
    try:
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps

        # Determine extraction points
        if is_start: # First 2 seconds
            start_f = 0
            end_f = int(min(total_frames, 2 * fps))
            if end_f <= start_f: end_f = total_frames # Handle short videos
        else: # Last 2 seconds
            start_f = int(max(0, total_frames - 2 * fps))
            end_f = total_frames
            if start_f >= end_f: start_f = 0
            
        frame_indices = np.linspace(start_f, end_f - 1, samples, dtype=int)
        frames = []
        
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                # Resize for token efficiency (Height 300px)
                h, w, _ = frame.shape
                scale = 300 / h
                new_w = int(w * scale)
                frame = cv2.resize(frame, (new_w, 300))
                frames.append(frame)
        cap.release()
        
        if not frames:
            raise ValueError("No frames extracted")
            
        # Stitch horizontally
        filmstrip = cv2.hconcat(frames)
        
        # Use a consistent temp file pattern or unique name
        temp_dir = tempfile.gettempdir()
        output_path = os.path.join(temp_dir, f"temp_strip_{int(time.time())}_{'start' if is_start else 'end'}.jpg")
        cv2.imwrite(output_path, filmstrip)
        return output_path
    except Exception as e:
        print(f"⚠️ Filmstrip failed: {e}")
        return None

# --- PHASE 1: ANALYZE ONLY ---
def analyze_only(video_a_path: str, video_c_path: str):
    print(f"🎬 Analyst: Processing videos...")

    # Generate Filmstrips
    strip_a = create_filmstrip(video_a_path, is_start=False)
    strip_c = create_filmstrip(video_c_path, is_start=True)
    
    if not strip_a or not strip_c:
        return {
            "prompt": "Cinematic transition between scenes.",
            "video_a_path": video_a_path,
            "video_c_path": video_c_path,
            "status": "warning",
            "detail": "Could not create filmstrips"
        }

    prompt = "Smooth cinematic transition." # Default safety

    # 1. Try Gemini 2.0 (Primary)
    try:
        print("🤖 Engaging Gemini 2.0...")
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY")) # Using correct env var name
        
        file_a = client.files.upload(file=strip_a)
        file_c = client.files.upload(file=strip_c)

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
        
        if response.text:
            prompt = response.text
            
        # raise Exception("Force Fallback for Testing") # Commented out for production use unless specifically testing
        
    except Exception as e:
        print(f"⚠️ Gemini Quota/Error: {e}. Switching to Llama 3.2 (Groq)...")
        
        # 2. Try Groq (Fallback)
        try:
            groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            
            def encode_image(image_path):
                with open(image_path, "rb") as image_file:
                    return base64.b64encode(image_file.read()).decode('utf-8')
            
            b64_a = encode_image(strip_a)
            b64_c = encode_image(strip_c)
            
            completion = groq_client.chat.completions.create(
                model="llama-3.2-11b-vision-instruct", 
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "These images show the END of Clip A and START of Clip C. Describe a smooth visual transition to bridge them."},
                            {
                                "type": "image_url", 
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_a}"}
                            },
                             {
                                "type": "image_url", 
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_c}"}
                            }
                        ]
                    }
                ],
                temperature=0.7,
                max_tokens=500
            )
            prompt = completion.choices[0].message.content
        except Exception as groq_e:
            print(f"❌ Groq also failed: {groq_e}. Using default prompt.")

    # Cleanup
    try:
        if os.path.exists(strip_a): os.remove(strip_a)
        if os.path.exists(strip_c): os.remove(strip_c)
    except:
        pass

    return {
        "prompt": prompt,
        "video_a_path": video_a_path,
        "video_c_path": video_c_path,
        "status": "success"
    }

# --- PHASE 2: GENERATE ONLY ---
def generate_only(prompt: str, video_a_path: str, video_c_path: str):
    print(f"🎥 Generator: Action! Prompt: {prompt[:50]}...")

    # 1. Primary: Wan 2.2
    try:
        # Extract Frames for Wan
        # We need to save temporary frames because handle_file expects a path
        def get_frame(v_path, at_start):
            cap = cv2.VideoCapture(v_path)
            if not at_start:
                 total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                 cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total-1))
            ret, frame = cap.read()
            cap.release()
            if not ret: raise ValueError("Frame extract failed")
            
            # Resize safe for Wan
            h, w = frame.shape[:2]
            if h > 480:
                scale = 480/h
                frame = cv2.resize(frame, (int(w*scale), 480))
            
            t_path = os.path.join(tempfile.gettempdir(), f"wan_frame_{int(time.time())}_{'s' if at_start else 'e'}.png")
            cv2.imwrite(t_path, frame)
            return t_path

        f_start = get_frame(video_a_path, False) # Last frame of A
        f_end = get_frame(video_c_path, True)    # First frame of C

        client = Client("multimodalart/wan-2-2-first-last-frame", token=os.environ.get("HF_TOKEN"))
        
        print("Generating with Wan 2.2...")
        result = client.predict(
            start_image_pil=handle_file(f_start),
            end_image_pil=handle_file(f_end),
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
        
        # Cleanup temp
        try:
            os.remove(f_start)
            os.remove(f_end)
        except: pass

        # Parse result
        video_out = result[0]
        if isinstance(video_out, dict) and 'video' in video_out:
             return {"video_url": video_out['video']}
        elif isinstance(video_out, str):
             return {"video_url": video_out}
        else:
             raise ValueError(f"Unknown Wan output: {result}")

    except Exception as e:
        print(f"⚠️ Wan 2.2 Failed (Quota/Error): {e}")
        print("🔄 Switching to SVD Fallback...")
        
        # 2. Fallback: SVD (Image-to-Video)
        try:
            client_svd = Client("stabilityai/stable-video-diffusion-img2vid-xt-1-1")
            
            # Extract last frame of A for SVD input
            cap = cv2.VideoCapture(video_a_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, total-1)
            ret, frame = cap.read()
            cap.release()
            
            # Resize for SVD (1024x576 recommended or similar 16:9)
            frame = cv2.resize(frame, (1024, 576))
            
            svd_input_path = os.path.join(tempfile.gettempdir(), "svd_input.jpg")
            cv2.imwrite(svd_input_path, frame)
            
            print("Generating with SVD...")
            result = client_svd.predict(
                svd_input_path,
                0.0, 127, 6, 
                api_name="/predict"
            )
            return {"video_url": result} 
            
        except Exception as svd_e:
            err_msg = f"All Generators Failed. Wan: {e}, SVD: {svd_e}"
            print(f"❌ {err_msg}")
            return {"video_url": f"Error: {err_msg}"}

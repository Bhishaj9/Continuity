from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os
import shutil
import uuid
from continuity_agent.agent import analyze_only, generate_only

app = FastAPI(title="Continuity", description="AI Video Bridging Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

@app.get("/")
async def read_root():
    return FileResponse("stitch_continuity_dashboard/code.html")

@app.post("/analyze")
async def analyze_endpoint(
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...)
):
    try:
        request_id = str(uuid.uuid4())
        ext_a = os.path.splitext(video_a.filename)[1] or ".mp4"
        ext_c = os.path.splitext(video_c.filename)[1] or ".mp4"

        path_a = os.path.join(OUTPUT_DIR, f"{request_id}_a{ext_a}")
        path_c = os.path.join(OUTPUT_DIR, f"{request_id}_c{ext_c}")
        
        with open(path_a, "wb") as buffer:
            shutil.copyfileobj(video_a.file, buffer)
        with open(path_c, "wb") as buffer:
            shutil.copyfileobj(video_c.file, buffer)
            
        # Call Agent
        result = analyze_only(os.path.abspath(path_a), os.path.abspath(path_c))
        
        if result.get("status") == "error":
             raise HTTPException(status_code=500, detail=result.get("detail"))
             
        return {
            "prompt": result["prompt"],
            "video_a_path": os.path.abspath(path_a),
            "video_c_path": os.path.abspath(path_c)
        }
    except Exception as e:
        print(f"Server Error (Analyze): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate")
async def generate_endpoint(
    prompt: str = Body(...),
    video_a_path: str = Body(...),
    video_c_path: str = Body(...)
):
    try:
        if not os.path.exists(video_a_path) or not os.path.exists(video_c_path):
             raise HTTPException(status_code=400, detail="Video files not found on server.")
             
        # Call Agent
        result = generate_only(prompt, video_a_path, video_c_path)
        gen_path = result.get("video_url")
        
        if not gen_path or "Error" in gen_path:
            raise HTTPException(status_code=500, detail=f"Generation failed: {gen_path}")
            
        # Move final file to output dir if it's not already there (SVD might return temp path)
        final_filename = f"{uuid.uuid4()}_bridge.mp4"
        final_output_path = os.path.join(OUTPUT_DIR, final_filename)
        
        # If gen_path is a URL (some providers), we might need to handle differently
        # But our agent functions return local paths (SVD) or temp paths (Wan)
        if os.path.exists(gen_path):
             shutil.move(gen_path, final_output_path)
        else:
             # Assume it's an error message or invalid
             raise HTTPException(status_code=500, detail="Generated file missing.")
        
        return {"video_url": f"/outputs/{final_filename}"}
        
    except Exception as e:
        print(f"Server Error (Generate): {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Server Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)

from fastapi import FastAPI, HTTPException, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os
import shutil
import uuid
from continuity_agent.agent import app as continuity_graph

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

@app.post("/generate-transition")
async def generate_transition(
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...),
    prompt: str = Form("Cinematic transition")
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
            
        initial_state = {
            "video_a_url": "local_upload",
            "video_c_url": "local_upload",
            "user_notes": prompt,
            "veo_prompt": prompt,
            "video_a_local_path": os.path.abspath(path_a),
            "video_c_local_path": os.path.abspath(path_c),
            "generated_video_url": "", 
            "status": "started"
        }
        
        result = continuity_graph.invoke(initial_state)
        gen_path = result.get("generated_video_url")
        
        if not gen_path or "Error" in gen_path:
            raise HTTPException(status_code=500, detail=f"Generation failed: {gen_path}")
            
        final_filename = f"{request_id}_bridge.mp4"
        final_output_path = os.path.join(OUTPUT_DIR, final_filename)
        shutil.move(gen_path, final_output_path)
        
        return {"video_url": f"/outputs/{final_filename}"}
    except Exception as e:
        print(f"Server Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)

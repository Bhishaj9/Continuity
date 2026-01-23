from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn, os, shutil, uuid, json, asyncio
from agent import analyze_only, generate_only
from utils import get_history_from_gcs

app = FastAPI(title="Continuity", description="AI Video Bridging Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

class JobQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_processing = False

    async def add_job(self, job_func, *args):
        await self.queue.put((job_func, args))
        if not self.is_processing:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        self.is_processing = True
        while not self.queue.empty():
            func, args = await self.queue.get()
            try:
                await asyncio.to_thread(func, *args)
            except Exception as e:
                print(f"Queue Error: {e}")
            self.queue.task_done()
        self.is_processing = False

job_queue = JobQueue()

@app.get("/")
def read_root():
    return FileResponse("stitch_continuity_dashboard/code.html")

@app.post("/analyze")
def analyze_endpoint(video_a: UploadFile = File(...), video_c: UploadFile = File(...)):
    try:
        rid = str(uuid.uuid4())
        pa = os.path.join("outputs", f"{rid}_a.mp4")
        pc = os.path.join("outputs", f"{rid}_c.mp4")
        
        with open(pa, "wb") as b:
            shutil.copyfileobj(video_a.file, b)
        with open(pc, "wb") as b:
            shutil.copyfileobj(video_c.file, b)
            
        res = analyze_only(os.path.abspath(pa), os.path.abspath(pc), job_id=rid)
        
        if res.get("status") == "error":
            raise HTTPException(500, res.get("detail"))
            
        return {
            "analysis_a": res.get("analysis_a"),
            "analysis_c": res.get("analysis_c"),
            "prompt": res["prompt"],
            "video_a_path": os.path.abspath(pa),
            "video_c_path": os.path.abspath(pc)
        }
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/generate")
async def generate_endpoint(
    prompt: str = Body(...),
    style: str = Body("Cinematic"),
    audio_prompt: str = Body("Cinematic"),
    negative_prompt: str = Body(""),
    guidance_scale: float = Body(5.0),
    motion_strength: int = Body(5),
    video_a_path: str = Body(...),
    video_c_path: str = Body(...)
):
    if not os.path.exists(video_a_path) or not os.path.exists(video_c_path):
        raise HTTPException(400, "Videos not found.")
        
    job_id = str(uuid.uuid4())
    with open(f"outputs/{job_id}.json", "w") as f:
        json.dump({"status": "queued", "progress": 0, "log": "Queued..."}, f)
        
    await job_queue.add_job(generate_only, prompt, video_a_path, video_c_path, job_id, style, audio_prompt, negative_prompt, guidance_scale, motion_strength)
    return {"job_id": job_id}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    path = f"outputs/{job_id}.json"
    if not os.path.exists(path):
        raise HTTPException(404, "Job not found")
    with open(path, "r") as f:
        return json.load(f)

@app.get("/history")
def get_history():
    return get_history_from_gcs()

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn
import os

from agent import app as agent_app

app = FastAPI(title="Continuity", description="AI Video Bridging Service")

class BridgeRequest(BaseModel):
    url_a: str
    url_c: str
    notes: Optional[str] = None

@app.post("/create-bridge")
async def create_bridge(request: BridgeRequest):
    """
    Orchestrates the creation of a bridge video between two input clips.
    """
    try:
        # Initialize LangGraph state
        initial_state = {
            "video_a_url": request.url_a,
            "video_c_url": request.url_c,
            "user_notes": request.notes,
            "scene_analysis": None,
            "veo_prompt": None,
            "generated_video_url": None
        }
        
        print(f"Starting bridge generation for: {request.url_a} -> {request.url_c}")
        
        # Invoke the graph
        result = agent_app.invoke(initial_state)
        
        video_url = result.get("generated_video_url")
        analysis = result.get("scene_analysis")
        
        # Check for error strings in the URL field as per agent logic
        if video_url and "Error" in video_url:
             raise HTTPException(status_code=500, detail=video_url)
             
        if not video_url:
             raise HTTPException(status_code=500, detail="Failed to generate video (No URL returned)")

        return {
            "video_url": video_url,
            "analysis_summary": analysis
        }

    except Exception as e:
        # Catch unexpected errors
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

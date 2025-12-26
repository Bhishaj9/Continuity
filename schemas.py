from pydantic import BaseModel, HttpUrl
from typing import Optional

class VideoInput(BaseModel):
    video_url_1: str
    video_url_2: str
    user_notes: Optional[str] = "Make it cinematic"

class VideoOutput(BaseModel):
    bridging_video_url: str

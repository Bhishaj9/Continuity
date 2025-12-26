from typing import TypedDict, Optional

class AgentState(TypedDict):
    video_1_url: str
    video_2_url: str
    analysis_1: Optional[str]
    analysis_2: Optional[str]
    bridging_prompt: Optional[str]
    generated_video_path: Optional[str]

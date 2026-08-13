from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    status: Literal["started"] = "started"


class ChunkMessage(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    status: Literal["connected", "thinking", "completed", "error"]
    session_id: Optional[str] = None
    message: Optional[str] = None

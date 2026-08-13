import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_user_from_token
from app.models.user import User
from app.postgres import SessionLocal
from app.schemas.chat import ChatRequest, ChatResponse, ChunkMessage, StatusMessage
from app.services.connection_manager import manager
from app.services.openai_service import stream_chat

router = APIRouter(tags=["chat"])

THINKING_DELAY_SECONDS = 1.5


async def _stream_to_websocket(session_id: str, prompt: str) -> None:
    try:
        await manager.send_json(
            session_id,
            StatusMessage(status="thinking", session_id=session_id).model_dump(),
        )
        await asyncio.sleep(THINKING_DELAY_SECONDS)

        async for delta in stream_chat(prompt):
            sent = await manager.send_json(
                session_id,
                ChunkMessage(content=delta).model_dump(),
            )
            if not sent:
                return

        await manager.send_json(
            session_id,
            StatusMessage(status="completed", session_id=session_id).model_dump(),
        )
    except Exception as exc:
        await manager.send_json(
            session_id,
            StatusMessage(
                status="error",
                session_id=session_id,
                message=str(exc),
            ).model_dump(),
        )


@router.websocket("/ws/chat/{session_id}")
async def chat_websocket(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(default=None),
) -> None:
    if not token:
        await websocket.close(code=1008, reason="Missing auth token")
        return

    db = SessionLocal()
    try:
        get_user_from_token(token, db)
    except HTTPException:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    finally:
        db.close()

    await manager.connect(session_id, websocket)

    await manager.send_json(
        session_id,
        StatusMessage(status="connected", session_id=session_id).model_dump(),
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id)


@router.post("/api/chat", response_model=ChatResponse, status_code=202)
async def chat(
    request: ChatRequest,
    _: User = Depends(get_current_user),
) -> ChatResponse:
    if not manager.is_connected(request.session_id):
        raise HTTPException(
            status_code=409,
            detail="WebSocket not connected for this session",
        )

    asyncio.create_task(_stream_to_websocket(request.session_id, request.prompt))

    return ChatResponse(session_id=request.session_id)

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.agent.llm_client import friendly_llm_error
from app.agent.runner import run_agent
from app.agent.tools import TOOL_LABELS, tool_label
from app.dependencies import get_current_user, get_user_from_token
from app.models.user import User
from app.postgres import SessionLocal
from app.schemas.chat import (
    A2UIMessage,
    ChatRequest,
    ChatResetRequest,
    ChatResponse,
    ChunkMessage,
    DashboardUIMessage,
    StatusMessage,
)
from app.services import chat_history
from app.services.connection_manager import manager

router = APIRouter(tags=["chat"])


async def _stream_to_websocket(
    session_id: str,
    prompt: str,
    user: User,
    request: ChatRequest,
) -> None:
    user_id = str(user.id)
    collected: list[str] = []

    async def on_tool_call(tool_name: str, arguments: Optional[dict] = None) -> None:
        args = arguments or {}
        label = tool_label(tool_name, args) if tool_name == "query_analytics" else TOOL_LABELS.get(
            tool_name, tool_name
        )
        await manager.send_json(
            session_id,
            StatusMessage(
                status="tool_call",
                session_id=session_id,
                tool=tool_name,
                message=label,
            ).model_dump(),
        )

    try:
        history = chat_history.get_history(session_id, user_id)
        chat_history.add_message(session_id, user_id, "user", prompt)

        await manager.send_json(
            session_id,
            StatusMessage(status="thinking", session_id=session_id).model_dump(),
        )

        explorer_config = {
            "breakdown_by": request.explorer_config.breakdown_by,
            "period": request.explorer_config.period,
            "view": request.explorer_config.view,
        }

        dashboard_context = None
        if request.dashboard_context:
            dashboard_context = request.dashboard_context.model_dump()

        async def on_dashboard_ui(actions: list) -> None:
            await manager.send_json(
                session_id,
                DashboardUIMessage(
                    actions=actions,
                    summary="Dashboard updated",
                ).model_dump(),
            )

        async def on_a2ui(surface_id: str, messages: list, summary: Optional[str]) -> None:
            await manager.send_json(
                session_id,
                A2UIMessage(
                    surface_id=surface_id,
                    messages=messages,
                    summary=summary,
                ).model_dump(),
            )

        disconnected = False
        async for delta in run_agent(
            prompt=prompt,
            history=history,
            user_id=user_id,
            filters=request.filters,
            active_tab=request.active_tab,
            explorer_config=explorer_config,
            dashboard_context=dashboard_context,
            on_tool_call=on_tool_call,
            on_dashboard_ui=on_dashboard_ui,
            on_a2ui=on_a2ui,
        ):
            collected.append(delta)
            sent = await manager.send_json(
                session_id,
                ChunkMessage(content=delta).model_dump(),
            )
            if not sent:
                disconnected = True
                break

        final_text = "".join(collected).strip()
        if final_text:
            chat_history.add_message(session_id, user_id, "assistant", final_text)

        if not disconnected:
            await manager.send_json(
                session_id,
                StatusMessage(status="completed", session_id=session_id).model_dump(),
            )
    except Exception as exc:
        from app.config import get_settings

        settings = get_settings()
        await manager.send_json(
            session_id,
            StatusMessage(
                status="error",
                session_id=session_id,
                message=friendly_llm_error(
                    exc,
                    has_openai_key=bool(settings.openai_api_key),
                    has_gemini_key=bool(settings.gemini_api_key),
                ),
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
    user: User = Depends(get_current_user),
) -> ChatResponse:
    if not manager.is_connected(request.session_id):
        raise HTTPException(
            status_code=409,
            detail="WebSocket not connected for this session",
        )

    asyncio.create_task(_stream_to_websocket(request.session_id, request.prompt, user, request))

    return ChatResponse(session_id=request.session_id)


@router.post("/api/chat/reset")
async def reset_chat(
    request: ChatResetRequest,
    user: User = Depends(get_current_user),
) -> dict:
    chat_history.clear_session(request.session_id, str(user.id))
    return {"session_id": request.session_id, "status": "cleared"}

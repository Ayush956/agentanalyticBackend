from __future__ import annotations

import uuid

from app.models.chat_message import ChatMessage as ChatMessageModel
from app.postgres import Base, SessionLocal, engine

HISTORY_LIMIT = 20


def ensure_chat_tables() -> None:
    Base.metadata.create_all(bind=engine, tables=[ChatMessageModel.__table__])


def _as_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


def get_history(session_id: str, user_id: str | uuid.UUID, limit: int = HISTORY_LIMIT) -> list[dict]:
    ensure_chat_tables()
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessageModel)
            .filter(
                ChatMessageModel.session_id == session_id,
                ChatMessageModel.user_id == _as_uuid(user_id),
                ChatMessageModel.role.in_(["user", "assistant"]),
            )
            .order_by(ChatMessageModel.created_at.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
        return [{"role": row.role, "content": row.content} for row in rows]
    finally:
        db.close()


def add_message(session_id: str, user_id: str | uuid.UUID, role: str, content: str) -> None:
    ensure_chat_tables()
    db = SessionLocal()
    try:
        db.add(
            ChatMessageModel(
                session_id=session_id,
                user_id=_as_uuid(user_id),
                role=role,
                content=content,
            )
        )
        db.commit()
    finally:
        db.close()


def clear_session(session_id: str, user_id: str | uuid.UUID) -> None:
    ensure_chat_tables()
    db = SessionLocal()
    try:
        db.query(ChatMessageModel).filter(
            ChatMessageModel.session_id == session_id,
            ChatMessageModel.user_id == _as_uuid(user_id),
        ).delete()
        db.commit()
    finally:
        db.close()

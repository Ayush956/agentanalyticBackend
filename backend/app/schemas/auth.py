from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.enums import TicketStatus, UserRole
from app.schemas.user import UserPublic


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserTicketSummary(BaseModel):
    """Mongo ticket summary attached to login response."""

    total: int
    ticket_numbers: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    """
    Returned after successful login:
    - user from Postgres
    - JWT token
    - ticket summary from Mongo (filtered by created_by = user.id)
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
    tickets: UserTicketSummary


class TokenPayload(BaseModel):
    """Decoded JWT payload."""

    sub: UUID
    role: UserRole
    username: str
    exp: int

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.enums import UserRole


class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: UserRole = UserRole.ANALYST
    is_active: bool = True


class UserCreate(UserBase):
    """Postgres insert — password is hashed before saving."""

    password: str = Field(min_length=6, max_length=128)
    id: Optional[UUID] = None


class UserInDB(UserBase):
    """Full Postgres user row (includes hashed password — never return in API)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hashed_password: str
    created_at: datetime


class UserPublic(BaseModel):
    """Safe user payload returned after login or from /auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime

    @field_validator("role", mode="before")
    @classmethod
    def parse_role(cls, value: Any) -> UserRole:
        if isinstance(value, UserRole):
            return value
        return UserRole(str(value))

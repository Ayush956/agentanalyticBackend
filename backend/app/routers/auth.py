from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.postgres import get_postgres_session
from app.schemas.auth import LoginRequest, LoginResponse
from app.services.auth_service import login

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def auth_login(
    credentials: LoginRequest,
    db: Session = Depends(get_postgres_session),
) -> LoginResponse:
    return login(db, credentials)

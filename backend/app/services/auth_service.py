from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_database
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserTicketSummary
from app.schemas.user import UserPublic
from app.utils.security import create_access_token, verify_password


def get_user_tickets_summary(user_id: str) -> UserTicketSummary:
    db = get_database()
    query = {"created_by": user_id}
    total = db.tickets.count_documents(query)
    ticket_numbers = [
        doc["ticket_number"]
        for doc in db.tickets.find(query, {"ticket_number": 1}).limit(10)
    ]
    return UserTicketSummary(total=total, ticket_numbers=ticket_numbers)


def login(db: Session, credentials: LoginRequest) -> LoginResponse:
    user = db.query(User).filter(User.email == credentials.email).first()

    if user is None or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )

    user_id = str(user.id)
    access_token, expires_in = create_access_token(
        user_id=user_id,
        username=user.username,
        role=user.role,
    )
    tickets = get_user_tickets_summary(user_id)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserPublic.model_validate(user),
        tickets=tickets,
    )

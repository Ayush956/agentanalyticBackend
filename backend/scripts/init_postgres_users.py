"""
Create users table in Postgres and insert two dummy users.

Usage (from backend/):
    python scripts/init_postgres_users.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from passlib.context import CryptContext

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models.user import User  # noqa: E402
from app.postgres import Base, SessionLocal, engine  # noqa: E402

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DUMMY_USERS = [
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "username": "ayush",
        "email": "executive@maruti.com",
        "password": "secret123",
        "full_name": "Executive User",
        "role": "executive",
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "username": "mishra",
        "email": "analyst@maruti.com",
        "password": "secret123",
        "full_name": "Analyst User",
        "role": "analyst",
    },
]


def init_users() -> None:
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        inserted = 0
        for item in DUMMY_USERS:
            existing = (
                db.query(User)
                .filter(
                    (User.id == item["id"])
                    | (User.username == item["username"])
                    | (User.email == item["email"])
                )
                .first()
            )
            if existing:
                existing.username = item["username"]
                existing.email = item["email"]
                existing.full_name = item["full_name"]
                existing.role = item["role"]
                existing.hashed_password = pwd_context.hash(item["password"])
                existing.is_active = True
                print(f"Updated: {item['username']} -> {existing.id}")
                continue

            user = User(
                id=item["id"],
                username=item["username"],
                email=item["email"],
                hashed_password=pwd_context.hash(item["password"]),
                full_name=item["full_name"],
                role=item["role"],
                is_active=True,
            )
            db.add(user)
            inserted += 1
            print(f"Inserted: {item['username']} -> {item['id']}")

        db.commit()
        print(f"Done. New users inserted: {inserted}")
    finally:
        db.close()


if __name__ == "__main__":
    init_users()

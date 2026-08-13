"""
Seed MongoDB tickets linked to Postgres users via created_by (same UUID).

Phase 1: 2 tickets per user (default)
Phase 2: python scripts/seed_mongo_tickets.py --per-user 100

Usage (from backend/):
    python scripts/seed_mongo_tickets.py
    python scripts/seed_mongo_tickets.py --per-user 100
    python scripts/seed_mongo_tickets.py --force
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import get_database  # noqa: E402

SEED_MARKER_ID = "tickets_seed_v1"

# Must match Postgres users from init_postgres_users.py
POSTGRES_USERS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "username": "ayush",
        "role": "executive",
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "username": "mishra",
        "role": "analyst",
    },
]

DIVISIONS = [
    "Quality",
    "Supply Chain",
    "HR & Admin",
    "Finance",
    "Manufacturing",
    "Marketing",
    "Digital Ent.",
    "Engineering",
]

DEPARTMENTS = [
    "Customer Quality Ops",
    "L&D - Strategy",
    "Brand - Strategy",
    "Plant Ops",
    "Procurement",
    "IT Infrastructure",
]

TICKET_TYPES = ["CAPEX", "OPEX", "Travel", "Vendor", "Policy"]
PAYMENTS = ["Advance", "Regular", "Reimbursement"]
WORKFLOWS = ["Standard Approval", "Fast Track", "Executive Review"]
DECISION_MATRICES = ["Matrix A", "Matrix B", "Matrix C"]
STATUSES = ["Closed", "Open", "Rejected", "Cancelled"]
STATUS_WEIGHTS = [0.62, 0.21, 0.09, 0.08]
SENDBACK_PROBABILITY = 0.22

CHECKERS = ["Alice Sharma", "Dev Mehta", "Priya Nair"]
APPROVERS = ["Carol Singh", "Eva Roy", "Sanjay Patel"]
CO_INITIATORS = ["Bob Kumar", "Neha Gupta", None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_ticket(
    ticket_index: int,
    user: dict,
    user_ticket_index: int,
) -> dict:
    vertical = random.choice(DIVISIONS)
    status = random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
    year = random.choice([2024, 2025])
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    created_at = datetime(
        year, month, day, random.randint(8, 18), 0, 0, tzinfo=timezone.utc
    )

    closed_at = None
    approval_time_days = None
    checker_time_days = None
    co_initiator_time_days = None
    approver_time_days = None

    if status == "Open":
        if random.random() < 0.4:
            approval_time_days = round(random.uniform(2, 8), 1)
            checker_time_days = round(approval_time_days * 0.35, 1)
            approver_time_days = round(approval_time_days * 0.45, 1)
            co_initiator_time_days = round(
                approval_time_days - checker_time_days - approver_time_days, 1
            )
    elif status in {"Closed", "Rejected", "Cancelled"}:
        approval_time_days = round(random.uniform(5, 18), 1)
        checker_time_days = round(approval_time_days * random.uniform(0.28, 0.38), 1)
        approver_time_days = round(approval_time_days * random.uniform(0.38, 0.52), 1)
        co_initiator_time_days = round(
            max(0.5, approval_time_days - checker_time_days - approver_time_days), 1
        )
        closed_at = created_at + timedelta(days=approval_time_days)

    has_sendback = random.random() < SENDBACK_PROBABILITY
    admin_l1_sendback_count = random.randint(1, 3) if has_sendback else 0

    co_initiator = random.choice(CO_INITIATORS)
    if co_initiator is None and status != "Open":
        co_initiator = random.choice([c for c in CO_INITIATORS if c is not None])

    return {
        "_id": ObjectId(),
        "ticket_number": f"TKT-{year}-{user['username'].upper()}-{user_ticket_index:03d}",
        "created_by": user["id"],  # Postgres user UUID (string)
        "owner_username": user["username"],
        "owner_role": user["role"],
        "division": vertical,
        "ticket_type": random.choice(TICKET_TYPES),
        "payment": random.choice(PAYMENTS),
        "workflow": random.choice(WORKFLOWS),
        "decision_matrix": random.choice(DECISION_MATRICES),
        "year": year,
        "vertical": vertical,
        "department": random.choice(DEPARTMENTS),
        "status": status,
        "checker": random.choice(CHECKERS),
        "co_initiator": co_initiator,
        "approver": random.choice(APPROVERS),
        "created_at": created_at,
        "closed_at": closed_at,
        "approval_time_days": approval_time_days,
        "checker_time_days": checker_time_days,
        "co_initiator_time_days": co_initiator_time_days,
        "approver_time_days": approver_time_days,
        "admin_l1_sendback_count": admin_l1_sendback_count,
    }


def create_indexes(db) -> None:
    db.tickets.create_index("ticket_number", unique=True)
    db.tickets.create_index("created_by")
    db.tickets.create_index([("created_by", 1), ("status", 1)])
    db.tickets.create_index([("year", 1), ("division", 1), ("status", 1)])


def seed(per_user: int, force: bool) -> None:
    db = get_database()
    seed_meta = db.seed_meta

    if seed_meta.find_one({"_id": SEED_MARKER_ID}) and not force:
        print("Ticket seed already applied. Use --force to wipe tickets and re-seed.")
        return

    if force:
        db.tickets.delete_many({})
        seed_meta.delete_one({"_id": SEED_MARKER_ID})

    create_indexes(db)

    tickets: list[dict] = []
    ticket_counter = 1

    for user in POSTGRES_USERS:
        for user_ticket_index in range(1, per_user + 1):
            tickets.append(build_ticket(ticket_counter, user, user_ticket_index))
            ticket_counter += 1

    db.tickets.insert_many(tickets)

    seed_meta.update_one(
        {"_id": SEED_MARKER_ID},
        {
            "$set": {
                "completed_at": utc_now(),
                "tickets_per_user": per_user,
                "total_tickets": len(tickets),
                "users": [u["username"] for u in POSTGRES_USERS],
            }
        },
        upsert=True,
    )

    print(f"Seeded MongoDB database '{db.name}' successfully.")
    print(f"  tickets inserted: {len(tickets)}")
    for user in POSTGRES_USERS:
        count = db.tickets.count_documents({"created_by": user["id"]})
        print(f"  {user['username']} ({user['id']}): {count} tickets")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Mongo tickets linked to Postgres users")
    parser.add_argument(
        "--per-user",
        type=int,
        default=2,
        help="Tickets to create per Postgres user (default: 2)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing tickets and re-seed",
    )
    args = parser.parse_args()

    if args.per_user < 1:
        raise SystemExit("--per-user must be at least 1")

    random.seed()
    seed(per_user=args.per_user, force=args.force)


if __name__ == "__main__":
    main()

"""
Append extra MongoDB tickets with a richer status mix (Closed, Open, Rejected,
Cancelled) and admin L1 send-backs — similar to the fuller executive dashboard.

Does NOT delete existing tickets. Safe to run multiple times.

Usage (from backend/):
    python scripts/append_mongo_tickets.py
    python scripts/append_mongo_tickets.py --per-user 60
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
from scripts.seed_mongo_tickets import (  # noqa: E402
    APPROVERS,
    CHECKERS,
    CO_INITIATORS,
    DECISION_MATRICES,
    DEPARTMENTS,
    DIVISIONS,
    PAYMENTS,
    POSTGRES_USERS,
    TICKET_TYPES,
    WORKFLOWS,
    create_indexes,
)

# Roughly matches the fuller dashboard screenshot (~62/21/9/8 split)
STATUS_WEIGHTS: list[tuple[str, float]] = [
    ("Closed", 0.62),
    ("Open", 0.21),
    ("Rejected", 0.09),
    ("Cancelled", 0.08),
]

SENDBACK_PROBABILITY = 0.22


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def pick_status() -> str:
    statuses, weights = zip(*STATUS_WEIGHTS)
    return random.choices(statuses, weights=weights, k=1)[0]


def next_ticket_sequence(db, user: dict) -> int:
    prefix = f"TKT-"
    existing = db.tickets.find(
        {"created_by": user["id"], "ticket_number": {"$regex": f"^{prefix}"}},
        {"ticket_number": 1},
    )
    max_seq = 0
    for doc in existing:
        parts = doc["ticket_number"].split("-")
        if len(parts) >= 4:
            try:
                max_seq = max(max_seq, int(parts[-1]))
            except ValueError:
                continue
    return max_seq + 1


def build_timing(status: str) -> dict:
    approval_time_days = None
    checker_time_days = None
    co_initiator_time_days = None
    approver_time_days = None
    closed_at = None

    if status == "Open":
        if random.random() < 0.4:
            approval_time_days = round(random.uniform(2, 8), 1)
            checker_time_days = round(approval_time_days * random.uniform(0.3, 0.45), 1)
            approver_time_days = round(approval_time_days * random.uniform(0.35, 0.5), 1)
            co_initiator_time_days = round(
                max(0.5, approval_time_days - checker_time_days - approver_time_days),
                1,
            )
        return {
            "closed_at": None,
            "approval_time_days": approval_time_days,
            "checker_time_days": checker_time_days,
            "co_initiator_time_days": co_initiator_time_days,
            "approver_time_days": approver_time_days,
        }

    approval_time_days = round(random.uniform(5, 18), 1)
    checker_time_days = round(approval_time_days * random.uniform(0.28, 0.38), 1)
    approver_time_days = round(approval_time_days * random.uniform(0.38, 0.52), 1)
    co_initiator_time_days = round(
        max(0.5, approval_time_days - checker_time_days - approver_time_days),
        1,
    )

    return {
        "approval_time_days": approval_time_days,
        "checker_time_days": checker_time_days,
        "co_initiator_time_days": co_initiator_time_days,
        "approver_time_days": approver_time_days,
        "closed_at": None,  # filled by caller after created_at is known
    }


def build_ticket(
    user: dict,
    sequence: int,
    ticket_index: int,
) -> dict:
    vertical = random.choice(DIVISIONS)
    status = pick_status()
    year = random.choice([2024, 2025])

    month = random.randint(1, 12)
    day = random.randint(1, 28)
    created_at = datetime(year, month, day, random.randint(8, 18), 0, 0, tzinfo=timezone.utc)

    timing = build_timing(status)
    if status != "Open":
        timing["closed_at"] = created_at + timedelta(days=timing["approval_time_days"])

    has_sendback = random.random() < SENDBACK_PROBABILITY
    admin_l1_sendback_count = random.randint(1, 3) if has_sendback else 0

    co_initiator = random.choice(CO_INITIATORS)
    if co_initiator is None and status != "Open":
        co_initiator = random.choice([c for c in CO_INITIATORS if c is not None])

    return {
        "_id": ObjectId(),
        "ticket_number": f"TKT-{year}-{user['username'].upper()}-{sequence:04d}",
        "created_by": user["id"],
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
        "closed_at": timing["closed_at"],
        "approval_time_days": timing["approval_time_days"],
        "checker_time_days": timing["checker_time_days"],
        "co_initiator_time_days": timing["co_initiator_time_days"],
        "approver_time_days": timing["approver_time_days"],
        "admin_l1_sendback_count": admin_l1_sendback_count,
    }


def append_tickets(per_user: int) -> None:
    db = get_database()
    create_indexes(db)

    tickets: list[dict] = []
    for user in POSTGRES_USERS:
        start_seq = next_ticket_sequence(db, user)
        for offset in range(per_user):
            tickets.append(build_ticket(user, start_seq + offset, offset))

    if not tickets:
        print("No tickets to insert.")
        return

    db.tickets.insert_many(tickets)

    print(f"Appended {len(tickets)} tickets to MongoDB database '{db.name}'.")
    for user in POSTGRES_USERS:
        count = db.tickets.count_documents({"created_by": user["id"]})
        status_rows = list(
            db.tickets.aggregate(
                [
                    {"$match": {"created_by": user["id"]}},
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        )
        sendbacks = db.tickets.count_documents(
            {"created_by": user["id"], "admin_l1_sendback_count": {"$gt": 0}}
        )
        print(f"\n  {user['username']} — total: {count}, send-backs: {sendbacks}")
        for row in status_rows:
            print(f"    {row['_id']}: {row['count']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append Mongo tickets with richer status distribution"
    )
    parser.add_argument(
        "--per-user",
        type=int,
        default=60,
        help="Extra tickets to add per Postgres user (default: 60)",
    )
    args = parser.parse_args()

    if args.per_user < 1:
        raise SystemExit("--per-user must be at least 1")

    random.seed()
    append_tickets(per_user=args.per_user)


if __name__ == "__main__":
    main()

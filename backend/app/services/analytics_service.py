from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Optional

from app.database import get_database
from app.schemas.analytics import (
    AdminSendbacksL1,
    AgingBucketItem,
    AnalyticsFilters,
    ClosureVolumeItem,
    DepartmentAvgItem,
    ExecutiveResponse,
    ExplorerDataItem,
    ExplorerResponse,
    FiltersResponse,
    MonthTrendItem,
    OperationalResponse,
    SendbackTrendItem,
    StatusCountItem,
    StatusOverview,
    TimingMetrics,
    VerticalAvgItem,
)

AGING_BUCKETS = ["0-7", "8-15", "16-30", "31-60", ">60"]
CLOSED_STATUSES = {"Closed", "Rejected", "Cancelled"}


def _round(value: Optional[float], digits: int = 1) -> float:
    if value is None:
        return 0.0
    return round(float(value), digits)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(statistics.median(values), 1)


def build_match_filter(user_id: str, filters: AnalyticsFilters) -> dict[str, Any]:
    match: dict[str, Any] = {"created_by": user_id}

    if filters.division:
        match["division"] = filters.division
    if filters.ticket_type:
        match["ticket_type"] = filters.ticket_type
    if filters.payment:
        match["payment"] = filters.payment
    if filters.workflow:
        match["workflow"] = filters.workflow
    if filters.status:
        match["status"] = filters.status
    if filters.decision_matrix:
        match["decision_matrix"] = filters.decision_matrix
    if filters.year is not None:
        match["year"] = filters.year

    return match


def _get_collection():
    return get_database().tickets


def _count_filtered(match: dict[str, Any]) -> int:
    return _get_collection().count_documents(match)


def _fetch_tickets(match: dict[str, Any], projection: Optional[dict] = None) -> list[dict]:
    cursor = _get_collection().find(match, projection)
    return list(cursor)


def get_filters(user_id: str) -> FiltersResponse:
    match = {"created_by": user_id}
    collection = _get_collection()

    def distinct(field: str) -> list:
        values = collection.distinct(field, match)
        return sorted(values, key=lambda v: (isinstance(v, str), v))

    years = distinct("year")
    return FiltersResponse(
        divisions=distinct("division"),
        ticket_types=distinct("ticket_type"),
        payments=distinct("payment"),
        workflows=distinct("workflow"),
        statuses=distinct("status"),
        decision_matrices=distinct("decision_matrix"),
        years=[int(y) for y in years],
    )


def get_executive(user_id: str, filters: AnalyticsFilters) -> ExecutiveResponse:
    match = build_match_filter(user_id, filters)
    collection = _get_collection()
    total = _count_filtered(match)

    status_rows = list(
        collection.aggregate(
            [
                {"$match": match},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )
    statuses = [
        StatusCountItem(
            status=row["_id"],
            count=row["count"],
            percentage=_round((row["count"] / total) * 100 if total else 0),
        )
        for row in status_rows
    ]

    sendback_count = collection.count_documents(
        {**match, "admin_l1_sendback_count": {"$gt": 0}}
    )
    admin_sendbacks = AdminSendbacksL1(
        count=sendback_count,
        percentage=_round((sendback_count / total) * 100 if total else 0),
    )

    tickets = _fetch_tickets(match)
    approval_vals = [t["approval_time_days"] for t in tickets if t.get("approval_time_days") is not None]
    checker_vals = [t["checker_time_days"] for t in tickets if t.get("checker_time_days") is not None]
    co_init_vals = [
        t["co_initiator_time_days"]
        for t in tickets
        if t.get("co_initiator_time_days") is not None
    ]
    approver_vals = [t["approver_time_days"] for t in tickets if t.get("approver_time_days") is not None]

    timing = TimingMetrics(
        avg_approval_time_days=_round(statistics.mean(approval_vals) if approval_vals else 0),
        median_approval_time_days=_median(approval_vals),
        avg_checker_time_days=_round(statistics.mean(checker_vals) if checker_vals else 0),
        median_checker_time_days=_median(checker_vals),
        avg_co_initiator_time_days=_round(statistics.mean(co_init_vals) if co_init_vals else 0),
        avg_approver_time_days=_round(statistics.mean(approver_vals) if approver_vals else 0),
        median_approver_time_days=_median(approver_vals),
    )

    trend_rows = list(
        collection.aggregate(
            [
                {
                    "$match": {
                        **match,
                        "closed_at": {"$ne": None},
                        "approval_time_days": {"$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {"format": "%Y-%m", "date": "$closed_at"}
                        },
                        "avg_days": {"$avg": "$approval_time_days"},
                        "ticket_count": {"$sum": 1},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    )
    approval_time_trend = [
        MonthTrendItem(
            month=row["_id"],
            avg_days=_round(row["avg_days"]),
            ticket_count=row["ticket_count"],
        )
        for row in trend_rows
    ]

    closure_rows = list(
        collection.aggregate(
            [
                {"$match": {**match, "status": "Closed", "closed_at": {"$ne": None}}},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {"format": "%Y-%m", "date": "$closed_at"}
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    )
    closure_volume = [
        ClosureVolumeItem(month=row["_id"], count=row["count"]) for row in closure_rows
    ]

    return ExecutiveResponse(
        total_filtered=total,
        status_overview=StatusOverview(
            statuses=statuses,
            admin_sendbacks_l1=admin_sendbacks,
        ),
        timing_metrics=timing,
        approval_time_trend=approval_time_trend,
        closure_volume_by_month=closure_volume,
    )


def _age_bucket(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 15:
        return "8-15"
    if age_days <= 30:
        return "16-30"
    if age_days <= 60:
        return "31-60"
    return ">60"


def get_operational(user_id: str, filters: AnalyticsFilters) -> OperationalResponse:
    match = build_match_filter(user_id, filters)
    collection = _get_collection()
    total = _count_filtered(match)
    now = datetime.now(timezone.utc)

    open_tickets = _fetch_tickets({**match, "status": "Open"}, {"created_at": 1})
    bucket_counts = {label: 0 for label in AGING_BUCKETS}
    for ticket in open_tickets:
        created_at = ticket["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created_at).days
        bucket_counts[_age_bucket(age_days)] += 1

    open_ticket_aging = [
        AgingBucketItem(bucket=label, count=bucket_counts[label]) for label in AGING_BUCKETS
    ]

    sendback_rows = list(
        collection.aggregate(
            [
                {"$match": {**match, "admin_l1_sendback_count": {"$gt": 0}}},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {"format": "%Y-%m", "date": "$created_at"}
                        },
                        "count": {"$sum": "$admin_l1_sendback_count"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    )
    admin_sendback_trend = [
        SendbackTrendItem(month=row["_id"], count=int(row["count"])) for row in sendback_rows
    ]

    vertical_rows = list(
        collection.aggregate(
            [
                {
                    "$match": {
                        **match,
                        "status": "Closed",
                        "approval_time_days": {"$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": "$vertical",
                        "avg_days": {"$avg": "$approval_time_days"},
                        "ticket_count": {"$sum": 1},
                    }
                },
                {"$sort": {"avg_days": -1}},
            ]
        )
    )
    approval_by_vertical = [
        VerticalAvgItem(
            vertical=row["_id"],
            avg_days=_round(row["avg_days"]),
            ticket_count=row["ticket_count"],
        )
        for row in vertical_rows
    ]

    department_rows = list(
        collection.aggregate(
            [
                {
                    "$match": {
                        **match,
                        "status": "Closed",
                        "approval_time_days": {"$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": "$department",
                        "avg_days": {"$avg": "$approval_time_days"},
                        "ticket_count": {"$sum": 1},
                    }
                },
                {"$sort": {"avg_days": -1}},
                {"$limit": 8},
            ]
        )
    )
    approval_by_department = [
        DepartmentAvgItem(
            department=row["_id"],
            avg_days=_round(row["avg_days"]),
            ticket_count=row["ticket_count"],
        )
        for row in department_rows
    ]

    return OperationalResponse(
        total_filtered=total,
        open_ticket_aging=open_ticket_aging,
        admin_sendback_trend=admin_sendback_trend,
        approval_time_by_vertical=approval_by_vertical,
        approval_time_by_department=approval_by_department,
    )


BREAKDOWN_FIELDS = {
    "vertical": "vertical",
    "division": "division",
    "department": "department",
    "ticket_type": "ticket_type",
}


def get_explorer(
    user_id: str,
    filters: AnalyticsFilters,
    measure: str = "approval_time",
    breakdown_by: str = "vertical",
    period: str = "month",
    view: str = "breakdown",
) -> ExplorerResponse:
    match = build_match_filter(user_id, filters)
    collection = _get_collection()
    total = _count_filtered(match)

    closed_match = {
        **match,
        "approval_time_days": {"$ne": None},
        "closed_at": {"$ne": None},
    }

    if view == "trend":
        date_format = "%Y-%m" if period == "month" else "%Y-%U"
        rows = list(
            collection.aggregate(
                [
                    {"$match": closed_match},
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": {"format": date_format, "date": "$closed_at"}
                            },
                            "value": {"$avg": "$approval_time_days"},
                            "ticket_count": {"$sum": 1},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ]
            )
        )
        data = [
            ExplorerDataItem(
                label=row["_id"],
                value=_round(row["value"]),
                ticket_count=row["ticket_count"],
            )
            for row in rows
        ]
        return ExplorerResponse(
            measure=measure,
            breakdown_by=breakdown_by,
            period=period,
            view=view,
            total_filtered=total,
            data=data,
        )

    field = BREAKDOWN_FIELDS.get(breakdown_by, "vertical")
    rows = list(
        collection.aggregate(
            [
                {"$match": closed_match},
                {
                    "$group": {
                        "_id": f"${field}",
                        "value": {"$avg": "$approval_time_days"},
                        "ticket_count": {"$sum": 1},
                    }
                },
                {"$sort": {"value": -1}},
            ]
        )
    )
    data = [
        ExplorerDataItem(
            label=row["_id"],
            value=_round(row["value"]),
            ticket_count=row["ticket_count"],
        )
        for row in rows
    ]

    return ExplorerResponse(
        measure=measure,
        breakdown_by=breakdown_by,
        period=period,
        view=view,
        total_filtered=total,
        data=data,
    )

from __future__ import annotations

import re
import statistics
from collections import defaultdict
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
    AnalyticsQueryResponse,
    AnalyticsQuerySegment,
    TicketCountBreakdownItem,
    TicketCountBreakdownResponse,
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
    "status": "status",
    "workflow": "workflow",
    "payment": "payment",
    "decision_matrix": "decision_matrix",
    "year": "year",
}

AVG_MEASURE_FIELDS = {
    "avg_approval_time": "approval_time_days",
    "avg_checker_time": "checker_time_days",
    "avg_approver_time": "approver_time_days",
    "avg_co_initiator_time": "co_initiator_time_days",
}

MEASURE_UNITS = {
    "count": "tickets",
    "sendback_ticket_count": "tickets",
    "total_sendbacks": "events",
    "avg_approval_time": "days",
    "avg_checker_time": "days",
    "avg_approver_time": "days",
    "avg_co_initiator_time": "days",
}


_MONTH_NAME_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _data_years(user_id: str) -> list[int]:
    years = _get_collection().distinct("year", {"created_by": user_id})
    return sorted(int(y) for y in years if y is not None)


def _coerce_month_tokens(raw_months: Any) -> list[Any]:
    if not raw_months:
        return []
    if isinstance(raw_months, str):
        return [
            part.strip()
            for part in re.split(r"[,/&]+|\band\b", raw_months, flags=re.I)
            if part.strip()
        ]
    if isinstance(raw_months, list):
        return raw_months
    return [raw_months]


def _parse_month_token(token: Any) -> tuple[Optional[str], Optional[int]]:
    """Return (YYYY-MM key, month number) — only one side set for each token."""
    if token is None:
        return None, None
    if isinstance(token, int) and 1 <= token <= 12:
        return None, token

    text = str(token).strip().lower()
    if not text:
        return None, None
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text, None
    if re.fullmatch(r"\d{1,2}", text):
        month_num = int(text)
        return (None, month_num) if 1 <= month_num <= 12 else (None, None)
    if text in _MONTH_NAME_TO_NUM:
        return None, _MONTH_NAME_TO_NUM[text]
    return None, None


def _dedupe_preserve_order(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _resolve_month_filter(
    user_id: str,
    filters: AnalyticsFilters,
    filter_overrides: Optional[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], list[str]]:
    if not filter_overrides or not filter_overrides.get("months"):
        cleaned = dict(filter_overrides) if filter_overrides else None
        return cleaned, []

    raw_months = _coerce_month_tokens(filter_overrides.get("months"))
    cleaned = {key: value for key, value in filter_overrides.items() if key != "months"}

    explicit_keys: list[str] = []
    month_numbers: list[int] = []
    for token in raw_months:
        month_key, month_num = _parse_month_token(token)
        if month_key:
            explicit_keys.append(month_key)
        elif month_num is not None and month_num not in month_numbers:
            month_numbers.append(month_num)

    if explicit_keys and not month_numbers:
        return (cleaned or None), _dedupe_preserve_order(sorted(explicit_keys))

    if not month_numbers:
        return (cleaned or None), _dedupe_preserve_order(explicit_keys)

    resolved_year = filters.year
    if resolved_year is None and filter_overrides.get("year") is not None:
        resolved_year = int(filter_overrides["year"])

    if resolved_year is not None:
        generated = [f"{resolved_year}-{month:02d}" for month in month_numbers]
    else:
        years = _data_years(user_id) or [datetime.now(timezone.utc).year]
        generated = [f"{year}-{month:02d}" for year in years for month in month_numbers]

    month_keys = _dedupe_preserve_order([*explicit_keys, *sorted(generated)])
    return (cleaned or None), month_keys


def _apply_month_date_filter(
    match: dict[str, Any], date_field: str, month_keys: list[str]
) -> dict[str, Any]:
    if not month_keys:
        return match

    ranges: list[dict[str, Any]] = []
    for ym in month_keys:
        year_str, month_str = ym.split("-", 1)
        year_num = int(year_str)
        month_num = int(month_str)
        start = datetime(year_num, month_num, 1, tzinfo=timezone.utc)
        if month_num == 12:
            end = datetime(year_num + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year_num, month_num + 1, 1, tzinfo=timezone.utc)
        ranges.append({date_field: {"$gte": start, "$lt": end}})

    if len(ranges) == 1:
        return {**match, **ranges[0]}
    return {"$and": [match, {"$or": ranges}]}


def _filter_segments_by_months(
    data: list[AnalyticsQuerySegment], month_keys: list[str]
) -> list[AnalyticsQuerySegment]:
    if not month_keys:
        return data
    allowed = set(month_keys)
    filtered = [item for item in data if item.label in allowed]
    existing = {item.label for item in filtered}
    for month_key in month_keys:
        if month_key not in existing:
            filtered.append(
                AnalyticsQuerySegment(
                    label=month_key,
                    value=0.0,
                    ticket_count=0,
                    percentage=0.0,
                )
            )
    filtered.sort(key=lambda item: item.label)
    return filtered


def _filters_applied_payload(filters: AnalyticsFilters, month_keys: list[str]) -> dict[str, Any]:
    payload = filters.model_dump(exclude_none=True)
    if month_keys:
        payload["months"] = month_keys
    return payload


def _merge_filters(base: AnalyticsFilters, overrides: Optional[dict[str, Any]]) -> AnalyticsFilters:
    if not overrides:
        return base
    payload = base.model_dump()
    for key, value in overrides.items():
        if key not in payload or value is None:
            continue
        if key == "year":
            payload[key] = int(value)
        else:
            payload[key] = str(value).strip() if value != "" else None
    return AnalyticsFilters(**payload)


def _measure_match(measure: str, match: dict[str, Any]) -> dict[str, Any]:
    if measure in AVG_MEASURE_FIELDS:
        field = AVG_MEASURE_FIELDS[measure]
        return {**match, field: {"$ne": None}}
    if measure == "sendback_ticket_count":
        return {**match, "admin_l1_sendback_count": {"$gt": 0}}
    if measure == "total_sendbacks":
        return {**match, "admin_l1_sendback_count": {"$gt": 0}}
    if measure == "count" and match.get("status") == "Closed":
        return match
    return match


def _date_field_for_breakdown(measure: str, breakdown_by: str) -> str:
    if breakdown_by not in {"month", "week"}:
        return "created_at"
    if measure in AVG_MEASURE_FIELDS or (
        measure == "count" and breakdown_by in {"month", "week"}
    ):
        return "closed_at"
    return "created_at"


def _compute_open_aging_segments(
    tickets: list[dict],
    primary_field: Optional[str],
) -> list[AnalyticsQuerySegment]:
    now = datetime.now(timezone.utc)
    if primary_field:
        nested: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        primary_totals: dict[str, int] = defaultdict(int)
        for ticket in tickets:
            created_at = ticket["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            bucket = _age_bucket((now - created_at).days)
            primary = str(ticket.get(primary_field) or "Unknown")
            nested[primary][bucket] += 1
            primary_totals[primary] += 1

        results: list[AnalyticsQuerySegment] = []
        for primary in sorted(primary_totals, key=lambda k: primary_totals[k], reverse=True):
            bucket_map = nested[primary]
            total = primary_totals[primary]
            results.append(
                AnalyticsQuerySegment(
                    label=primary,
                    value=float(total),
                    ticket_count=total,
                    percentage=_round((total / len(tickets)) * 100 if tickets else 0),
                    segments=[
                        AnalyticsQuerySegment(
                            label=bucket,
                            value=float(bucket_map.get(bucket, 0)),
                            ticket_count=bucket_map.get(bucket, 0),
                            percentage=_round(
                                (bucket_map.get(bucket, 0) / total) * 100 if total else 0
                            ),
                        )
                        for bucket in AGING_BUCKETS
                        if bucket_map.get(bucket, 0) > 0
                    ],
                )
            )
        return results

    bucket_counts = {label: 0 for label in AGING_BUCKETS}
    for ticket in tickets:
        created_at = ticket["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        bucket_counts[_age_bucket((now - created_at).days)] += 1

    total = len(tickets)
    return [
        AnalyticsQuerySegment(
            label=bucket,
            value=float(bucket_counts[bucket]),
            ticket_count=bucket_counts[bucket],
            percentage=_round((bucket_counts[bucket] / total) * 100 if total else 0),
        )
        for bucket in AGING_BUCKETS
        if bucket_counts[bucket] > 0
    ]


def query_analytics(
    user_id: str,
    filters: AnalyticsFilters,
    measure: str = "count",
    breakdown_by: Optional[str] = None,
    secondary_breakdown_by: Optional[str] = None,
    filter_overrides: Optional[dict[str, Any]] = None,
    limit: int = 50,
) -> AnalyticsQueryResponse:
    cleaned_overrides, month_keys = _resolve_month_filter(user_id, filters, filter_overrides)
    merged = _merge_filters(filters, cleaned_overrides)
    if measure == "count" and breakdown_by == "month" and merged.status is None:
        merged = AnalyticsFilters(**{**merged.model_dump(), "status": "Closed"})
    match = build_match_filter(user_id, merged)
    match = _measure_match(measure, match)
    if month_keys and breakdown_by not in {"month", "week"}:
        date_field = _date_field_for_breakdown(measure, "month")
        match = _apply_month_date_filter(match, date_field, month_keys)

    if breakdown_by == "aging_bucket" or secondary_breakdown_by == "aging_bucket":
        if merged.status is None:
            match = {**match, "status": "Open"}
        elif merged.status != "Open":
            match = {**match, "status": "Open"}

    collection = _get_collection()
    total_matching = _count_filtered(match)

    if breakdown_by == "aging_bucket" or (
        secondary_breakdown_by == "aging_bucket" and breakdown_by
    ):
        primary_field = BREAKDOWN_FIELDS.get(breakdown_by) if breakdown_by else None
        projection = {"created_at": 1}
        if primary_field:
            projection[primary_field] = 1
        tickets = _fetch_tickets({**match, "status": "Open"}, projection)
        data = _compute_open_aging_segments(tickets, primary_field)
        total_value = float(len(tickets))
        return AnalyticsQueryResponse(
            measure=measure,
            breakdown_by=breakdown_by or "aging_bucket",
            secondary_breakdown_by=secondary_breakdown_by,
            unit=MEASURE_UNITS.get(measure, ""),
            filters_applied=_filters_applied_payload(merged, month_keys),
            total_matching=total_matching,
            total_value=total_value,
            data=data,
        )

    if not breakdown_by:
        if measure == "count":
            total_value = float(total_matching)
            return AnalyticsQueryResponse(
                measure=measure,
                unit=MEASURE_UNITS.get(measure, ""),
                filters_applied=_filters_applied_payload(merged, month_keys),
                total_matching=total_matching,
                total_value=total_value,
                data=[
                    AnalyticsQuerySegment(
                        label="total",
                        value=total_value,
                        ticket_count=int(total_value),
                        percentage=100.0 if total_value else 0.0,
                    )
                ],
            )

        tickets = _fetch_tickets(match)
        if measure == "total_sendbacks":
            total_value = float(
                sum(int(t.get("admin_l1_sendback_count") or 0) for t in tickets)
            )
        elif measure in AVG_MEASURE_FIELDS:
            field = AVG_MEASURE_FIELDS[measure]
            values = [float(t[field]) for t in tickets if t.get(field) is not None]
            total_value = _round(statistics.mean(values) if values else 0)
        elif measure == "sendback_ticket_count":
            total_value = float(
                sum(1 for t in tickets if int(t.get("admin_l1_sendback_count") or 0) > 0)
            )
        else:
            total_value = float(total_matching)

        return AnalyticsQueryResponse(
            measure=measure,
            unit=MEASURE_UNITS.get(measure, ""),
            filters_applied=_filters_applied_payload(merged, month_keys),
            total_matching=total_matching,
            total_value=total_value,
            data=[
                AnalyticsQuerySegment(
                    label="total",
                    value=total_value,
                    ticket_count=total_matching,
                    percentage=100.0 if total_matching else 0.0,
                )
            ],
        )

    if secondary_breakdown_by and secondary_breakdown_by != "aging_bucket":
        primary_field = BREAKDOWN_FIELDS.get(breakdown_by)
        secondary_field = BREAKDOWN_FIELDS.get(secondary_breakdown_by)
        if not primary_field or not secondary_field:
            breakdown_by = breakdown_by or "department"
            secondary_breakdown_by = None
        else:
            rows = list(
                collection.aggregate(
                    [
                        {"$match": match},
                        {
                            "$group": {
                                "_id": {
                                    "primary": f"${primary_field}",
                                    "secondary": f"${secondary_field}",
                                },
                                "ticket_count": {"$sum": 1},
                                **(
                                    {"value": {"$sum": "$admin_l1_sendback_count"}}
                                    if measure == "total_sendbacks"
                                    else (
                                        {
                                            "value": {
                                                "$avg": f"${AVG_MEASURE_FIELDS[measure]}"
                                            }
                                        }
                                        if measure in AVG_MEASURE_FIELDS
                                        else {"value": {"$sum": 1}}
                                    )
                                ),
                            }
                        },
                        {"$sort": {"ticket_count": -1}},
                    ]
                )
            )
            grouped: dict[str, list[AnalyticsQuerySegment]] = defaultdict(list)
            primary_totals: dict[str, float] = defaultdict(float)
            for row in rows:
                primary = str(row["_id"].get("primary") or "Unknown")
                secondary = str(row["_id"].get("secondary") or "Unknown")
                ticket_count = int(row["ticket_count"])
                value = (
                    float(row["value"])
                    if measure in AVG_MEASURE_FIELDS
                    else float(int(row["value"]))
                )
                if measure in AVG_MEASURE_FIELDS:
                    value = _round(value)
                grouped[primary].append(
                    AnalyticsQuerySegment(
                        label=secondary,
                        value=value,
                        ticket_count=ticket_count,
                    )
                )
                primary_totals[primary] += ticket_count if measure != "total_sendbacks" else value

            data = []
            for primary in sorted(primary_totals, key=lambda k: primary_totals[k], reverse=True)[
                :limit
            ]:
                segments = grouped[primary]
                primary_count = sum(seg.ticket_count for seg in segments)
                primary_value = (
                    _round(statistics.mean([seg.value for seg in segments if seg.value]))
                    if measure in AVG_MEASURE_FIELDS
                    else float(primary_count)
                )
                if measure == "total_sendbacks":
                    primary_value = float(sum(seg.value for seg in segments))
                data.append(
                    AnalyticsQuerySegment(
                        label=primary,
                        value=primary_value,
                        ticket_count=primary_count,
                        percentage=_round(
                            (primary_count / total_matching) * 100 if total_matching else 0
                        ),
                        segments=segments,
                    )
                )

            total_value = float(total_matching)
            if measure in AVG_MEASURE_FIELDS:
                all_values = [row.value for row in data]
                total_value = _round(statistics.mean(all_values) if all_values else 0)
            elif measure == "total_sendbacks":
                total_value = float(sum(row.value for row in data))

            return AnalyticsQueryResponse(
                measure=measure,
                breakdown_by=breakdown_by,
                secondary_breakdown_by=secondary_breakdown_by,
                unit=MEASURE_UNITS.get(measure, ""),
                filters_applied=_filters_applied_payload(merged, month_keys),
                total_matching=total_matching,
                total_value=total_value,
                data=data,
            )

    if breakdown_by in {"month", "week"}:
        date_field = _date_field_for_breakdown(measure, breakdown_by)
        date_format = "%Y-%m" if breakdown_by == "month" else "%Y-%U"
        if month_keys and breakdown_by == "month":
            date_match = _apply_month_date_filter(match, date_field, month_keys)
        else:
            date_match = {**match, date_field: {"$ne": None}}
        group_stage: dict[str, Any] = {
            "_id": {
                "$dateToString": {"format": date_format, "date": f"${date_field}"}
            },
            "ticket_count": {"$sum": 1},
        }
        if measure == "total_sendbacks":
            group_stage["value"] = {"$sum": "$admin_l1_sendback_count"}
        elif measure in AVG_MEASURE_FIELDS:
            group_stage["value"] = {"$avg": f"${AVG_MEASURE_FIELDS[measure]}"}
        else:
            group_stage["value"] = {"$sum": 1}

        rows = list(
            collection.aggregate(
                [
                    {"$match": date_match},
                    {"$group": group_stage},
                    {"$sort": {"_id": 1}},
                    {"$limit": limit},
                ]
            )
        )
        data = [
            AnalyticsQuerySegment(
                label=str(row["_id"]),
                value=_round(float(row["value"]))
                if measure in AVG_MEASURE_FIELDS
                else float(int(row["value"])),
                ticket_count=int(row["ticket_count"]),
            )
            for row in rows
        ]
        if month_keys and breakdown_by == "month":
            data = _filter_segments_by_months(data, month_keys)
        total_value = float(sum(item.value for item in data))
        if measure in AVG_MEASURE_FIELDS and data:
            total_value = _round(
                statistics.mean([item.value for item in data if item.ticket_count > 0])
            )
        return AnalyticsQueryResponse(
            measure=measure,
            breakdown_by=breakdown_by,
            unit=MEASURE_UNITS.get(measure, ""),
            filters_applied=_filters_applied_payload(merged, month_keys),
            total_matching=total_matching,
            total_value=total_value,
            data=data,
        )

    field = BREAKDOWN_FIELDS.get(breakdown_by or "", "department")
    group_stage = {
        "_id": f"${field}",
        "ticket_count": {"$sum": 1},
    }
    if measure == "total_sendbacks":
        group_stage["value"] = {"$sum": "$admin_l1_sendback_count"}
    elif measure in AVG_MEASURE_FIELDS:
        group_stage["value"] = {"$avg": f"${AVG_MEASURE_FIELDS[measure]}"}
    else:
        group_stage["value"] = {"$sum": 1}

    rows = list(
        collection.aggregate(
            [
                {"$match": match},
                {"$group": group_stage},
                {"$sort": {"value": -1}},
                {"$limit": limit},
            ]
        )
    )

    total_for_pct = total_matching if measure != "total_sendbacks" else max(
        1, sum(int(row["value"]) for row in rows)
    )
    data = []
    for row in rows:
        ticket_count = int(row["ticket_count"])
        raw_value = float(row["value"])
        value = _round(raw_value) if measure in AVG_MEASURE_FIELDS else raw_value
        data.append(
            AnalyticsQuerySegment(
                label=str(row["_id"] or "Unknown"),
                value=value,
                ticket_count=ticket_count,
                percentage=_round((ticket_count / total_for_pct) * 100 if total_for_pct else 0)
                if measure in {"count", "sendback_ticket_count"}
                else None,
            )
        )

    if measure in AVG_MEASURE_FIELDS:
        values = [row.value for row in data if row.ticket_count > 0]
        total_value = _round(statistics.mean(values) if values else 0)
    elif measure == "total_sendbacks":
        total_value = float(sum(row.value for row in data))
    else:
        total_value = float(sum(row.ticket_count for row in data))

    return AnalyticsQueryResponse(
        measure=measure,
        breakdown_by=breakdown_by,
        unit=MEASURE_UNITS.get(measure, ""),
        filters_applied=_filters_applied_payload(merged, month_keys),
        total_matching=total_matching,
        total_value=total_value,
        data=data,
    )


def get_ticket_count_breakdown(
    user_id: str,
    filters: AnalyticsFilters,
    breakdown_by: str = "department",
    status_filter: Optional[str] = None,
) -> TicketCountBreakdownResponse:
    match = build_match_filter(user_id, filters)
    if status_filter:
        match["status"] = status_filter

    field = BREAKDOWN_FIELDS.get(breakdown_by, "department")
    collection = _get_collection()
    total = _count_filtered(match)

    rows = list(
        collection.aggregate(
            [
                {"$match": match},
                {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )
    data = [
        TicketCountBreakdownItem(
            label=row["_id"] or "Unknown",
            count=row["count"],
            percentage=_round((row["count"] / total) * 100 if total else 0),
        )
        for row in rows
    ]

    return TicketCountBreakdownResponse(
        breakdown_by=breakdown_by,
        status_filter=status_filter,
        total_matching=total,
        data=data,
    )


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

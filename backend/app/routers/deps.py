from typing import Optional

from fastapi import Query

from app.schemas.analytics import AnalyticsFilters


def _normalize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() == "all":
        return None
    return cleaned


def parse_analytics_filters(
    division: Optional[str] = Query(default=None),
    ticket_type: Optional[str] = Query(default=None),
    payment: Optional[str] = Query(default=None),
    workflow: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    decision_matrix: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
) -> AnalyticsFilters:
    return AnalyticsFilters(
        division=_normalize(division),
        ticket_type=_normalize(ticket_type),
        payment=_normalize(payment),
        workflow=_normalize(workflow),
        status=_normalize(status),
        decision_matrix=_normalize(decision_matrix),
        year=year,
    )

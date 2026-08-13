from fastapi import APIRouter, Depends, Query

from app.dependencies import get_current_user
from app.models.user import User
from app.routers.deps import parse_analytics_filters
from app.schemas.analytics import (
    AnalyticsFilters,
    ExecutiveResponse,
    ExplorerResponse,
    FiltersResponse,
    OperationalResponse,
)
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/filters", response_model=FiltersResponse)
def get_filters(current_user: User = Depends(get_current_user)) -> FiltersResponse:
    return analytics_service.get_filters(str(current_user.id))


@router.get("/executive", response_model=ExecutiveResponse)
def get_executive(
    filters: AnalyticsFilters = Depends(parse_analytics_filters),
    current_user: User = Depends(get_current_user),
) -> ExecutiveResponse:
    return analytics_service.get_executive(str(current_user.id), filters)


@router.get("/operational", response_model=OperationalResponse)
def get_operational(
    filters: AnalyticsFilters = Depends(parse_analytics_filters),
    current_user: User = Depends(get_current_user),
) -> OperationalResponse:
    return analytics_service.get_operational(str(current_user.id), filters)


@router.get("/explorer", response_model=ExplorerResponse)
def get_explorer(
    measure: str = Query(default="approval_time"),
    breakdown_by: str = Query(default="vertical"),
    period: str = Query(default="month", pattern="^(month|week)$"),
    view: str = Query(default="breakdown", pattern="^(breakdown|trend)$"),
    filters: AnalyticsFilters = Depends(parse_analytics_filters),
    current_user: User = Depends(get_current_user),
) -> ExplorerResponse:
    return analytics_service.get_explorer(
        str(current_user.id),
        filters,
        measure=measure,
        breakdown_by=breakdown_by,
        period=period,
        view=view,
    )

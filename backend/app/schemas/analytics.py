from typing import Optional

from pydantic import BaseModel, Field


class AnalyticsFilters(BaseModel):
    division: Optional[str] = None
    ticket_type: Optional[str] = None
    payment: Optional[str] = None
    workflow: Optional[str] = None
    status: Optional[str] = None
    decision_matrix: Optional[str] = None
    year: Optional[int] = None


class StatusCountItem(BaseModel):
    status: str
    count: int
    percentage: float


class AdminSendbacksL1(BaseModel):
    count: int
    percentage: float


class StatusOverview(BaseModel):
    statuses: list[StatusCountItem]
    admin_sendbacks_l1: AdminSendbacksL1


class TimingMetrics(BaseModel):
    avg_approval_time_days: float
    median_approval_time_days: float
    avg_checker_time_days: float
    median_checker_time_days: float
    avg_co_initiator_time_days: float
    avg_approver_time_days: float
    median_approver_time_days: float


class MonthTrendItem(BaseModel):
    month: str
    avg_days: float
    ticket_count: int


class ClosureVolumeItem(BaseModel):
    month: str
    count: int


class ExecutiveResponse(BaseModel):
    total_filtered: int
    status_overview: StatusOverview
    timing_metrics: TimingMetrics
    approval_time_trend: list[MonthTrendItem]
    closure_volume_by_month: list[ClosureVolumeItem]


class AgingBucketItem(BaseModel):
    bucket: str
    count: int


class SendbackTrendItem(BaseModel):
    month: str
    count: int


class VerticalAvgItem(BaseModel):
    vertical: str
    avg_days: float
    ticket_count: int


class DepartmentAvgItem(BaseModel):
    department: str
    avg_days: float
    ticket_count: int


class OperationalResponse(BaseModel):
    total_filtered: int
    open_ticket_aging: list[AgingBucketItem]
    admin_sendback_trend: list[SendbackTrendItem]
    approval_time_by_vertical: list[VerticalAvgItem]
    approval_time_by_department: list[DepartmentAvgItem]


class ExplorerDataItem(BaseModel):
    label: str
    value: float
    ticket_count: int


class ExplorerResponse(BaseModel):
    measure: str
    breakdown_by: str
    period: str
    view: str
    total_filtered: int
    unit: str = "d"
    data: list[ExplorerDataItem]


class FiltersResponse(BaseModel):
    divisions: list[str]
    ticket_types: list[str]
    payments: list[str]
    workflows: list[str]
    statuses: list[str]
    decision_matrices: list[str]
    years: list[int]

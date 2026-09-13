from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.analytics import AnalyticsFilters


class ExplorerConfigInput(BaseModel):
    breakdown_by: str = Field(default="vertical")
    period: str = Field(default="month")
    view: str = Field(default="breakdown")


class DashboardWidgetState(BaseModel):
    id: str
    visible: bool = True
    props: dict[str, Any] = Field(default_factory=dict)


class DashboardContextInput(BaseModel):
    active_tab: Optional[str] = None
    widgets: list[DashboardWidgetState] = Field(default_factory=list)
    surface_ids: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    active_tab: Optional[str] = None
    explorer_config: ExplorerConfigInput = Field(default_factory=ExplorerConfigInput)
    dashboard_context: Optional[DashboardContextInput] = None


class ChatResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    status: Literal["started"] = "started"


class ChunkMessage(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    status: Literal["connected", "thinking", "tool_call", "completed", "error"]
    session_id: Optional[str] = None
    message: Optional[str] = None
    tool: Optional[str] = None


class DashboardUIMessage(BaseModel):
    type: Literal["dashboard_ui"] = "dashboard_ui"
    actions: list[dict[str, Any]]
    summary: Optional[str] = None


class A2UIMessage(BaseModel):
    type: Literal["a2ui"] = "a2ui"
    surface_id: str
    messages: list[dict[str, Any]]
    summary: Optional[str] = None

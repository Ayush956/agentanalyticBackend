from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import TicketStatus, UserRole


def _parse_object_id(value: Any) -> str:
    return str(value)


class TicketBase(BaseModel):
    ticket_number: str = Field(min_length=1, max_length=50)
    created_by: str = Field(description="Postgres user UUID as string")
    owner_username: str
    owner_role: UserRole
    division: str
    ticket_type: str
    payment: str
    workflow: str
    decision_matrix: str
    year: int = Field(ge=2000, le=2100)
    vertical: str
    department: str
    status: TicketStatus
    checker: str
    co_initiator: Optional[str] = None
    approver: str
    created_at: datetime
    closed_at: Optional[datetime] = None
    approval_time_days: Optional[float] = None
    checker_time_days: Optional[float] = None
    co_initiator_time_days: Optional[float] = None
    approver_time_days: Optional[float] = None
    admin_l1_sendback_count: int = Field(default=0, ge=0)

    @field_validator("status", "owner_role", mode="before")
    @classmethod
    def parse_enums(cls, value: Any) -> Any:
        return value


class TicketCreate(TicketBase):
    """Mongo insert — used when creating a ticket for a logged-in user."""

    pass


class TicketDocument(TicketBase):
    """
    Full MongoDB ticket document as stored in the `tickets` collection.
    Matches seeded documents in analyticsagent.tickets.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")

    @field_validator("id", mode="before")
    @classmethod
    def object_id_to_str(cls, value: Any) -> str:
        return _parse_object_id(value)

    @classmethod
    def from_mongo(cls, document: dict) -> "TicketDocument":
        doc = dict(document)
        if "_id" in doc:
            doc["_id"] = _parse_object_id(doc["_id"])
        return cls.model_validate(doc)


class TicketResponse(BaseModel):
    """API-safe ticket payload (no internal mongo _id required in all endpoints)."""

    id: str
    ticket_number: str
    created_by: str
    owner_username: str
    owner_role: UserRole
    division: str
    ticket_type: str
    payment: str
    workflow: str
    decision_matrix: str
    year: int
    vertical: str
    department: str
    status: TicketStatus
    checker: str
    co_initiator: Optional[str] = None
    approver: str
    created_at: datetime
    closed_at: Optional[datetime] = None
    approval_time_days: Optional[float] = None
    checker_time_days: Optional[float] = None
    co_initiator_time_days: Optional[float] = None
    approver_time_days: Optional[float] = None
    admin_l1_sendback_count: int

    @classmethod
    def from_document(cls, document: TicketDocument) -> "TicketResponse":
        return cls(
            id=document.id,
            ticket_number=document.ticket_number,
            created_by=document.created_by,
            owner_username=document.owner_username,
            owner_role=document.owner_role,
            division=document.division,
            ticket_type=document.ticket_type,
            payment=document.payment,
            workflow=document.workflow,
            decision_matrix=document.decision_matrix,
            year=document.year,
            vertical=document.vertical,
            department=document.department,
            status=document.status,
            checker=document.checker,
            co_initiator=document.co_initiator,
            approver=document.approver,
            created_at=document.created_at,
            closed_at=document.closed_at,
            approval_time_days=document.approval_time_days,
            checker_time_days=document.checker_time_days,
            co_initiator_time_days=document.co_initiator_time_days,
            approver_time_days=document.approver_time_days,
            admin_l1_sendback_count=document.admin_l1_sendback_count,
        )


class TicketListResponse(BaseModel):
    total: int
    items: list[TicketResponse]

from enum import Enum


class TicketStatus(str, Enum):
    CLOSED = "Closed"
    OPEN = "Open"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"


class UserRole(str, Enum):
    EXECUTIVE = "executive"
    ANALYST = "analyst"

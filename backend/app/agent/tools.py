from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from app.agent.a2ui_builder import build_analytics_surface_messages, build_ticket_list_surface
from app.agent.widget_catalog import validate_action
from app.database import get_database
from app.schemas.analytics import AnalyticsFilters
from app.services import analytics_service
from app.services.analytics_service import build_match_filter

MEASURES = [
    "count",
    "avg_approval_time",
    "avg_checker_time",
    "avg_approver_time",
    "avg_co_initiator_time",
    "sendback_ticket_count",
    "total_sendbacks",
]

BREAKDOWNS = [
    "department",
    "vertical",
    "division",
    "ticket_type",
    "status",
    "workflow",
    "payment",
    "decision_matrix",
    "year",
    "month",
    "week",
    "aging_bucket",
]

FILTER_OVERRIDE_PROPS = {
    "status": {"type": "string", "description": "e.g. Closed, Open, Rejected"},
    "division": {"type": "string"},
    "ticket_type": {"type": "string"},
    "payment": {"type": "string"},
    "workflow": {"type": "string"},
    "decision_matrix": {"type": "string"},
    "year": {"type": "integer", "description": "Calendar year when user names months without a year"},
    "months": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Limit to specific months only. Use names or YYYY-MM, e.g. "
            "['Jan','Feb'] or ['2025-01','2025-02']. REQUIRED when user names months."
        ),
    },
}

INCLUDE_MONTHS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Specific months the user asked for (Jan, Feb, etc.). Uses dashboard year "
        "unless filter_overrides.year is set."
    ),
}

DASHBOARD_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["hide", "show", "configure", "navigate", "remove_surface"],
        },
        "widget_id": {"type": "string"},
        "surface_id": {"type": "string"},
        "tab": {
            "type": "string",
            "enum": ["executive", "operational", "approval-time", "ai-insights"],
        },
        "props": {
            "type": "object",
            "properties": {
                "showDataLabels": {"type": "boolean"},
                "showGrid": {"type": "boolean"},
                "showLegend": {"type": "boolean"},
                "chartType": {
                    "type": "string",
                    "enum": ["pie", "column", "bar", "line"],
                    "description": "Change chart type on built-in widgets",
                },
            },
        },
    },
    "required": ["action"],
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "update_dashboard_ui",
            "description": (
                "Change the LIVE dashboard the user is viewing. Use for: hide/remove/show "
                "existing charts, configure labels or legend, switch tabs, remove an "
                "agent-added A2UI surface. Do NOT use for brand-new charts with data — "
                "use add_analytics_surface instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": DASHBOARD_ACTION_SCHEMA,
                        "description": "One or more dashboard mutations to apply",
                    }
                },
                "required": ["actions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_analytics_surface",
            "description": (
                "Add a NEW chart, table, or metric card to the dashboard using A2UI. "
                "Fetches live analytics data and renders a new agent-added surface. Use when "
                "the user asks to add/show/create a chart or table with data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "measure": {"type": "string", "enum": MEASURES},
                    "breakdown_by": {"type": "string", "enum": BREAKDOWNS},
                    "secondary_breakdown_by": {"type": "string", "enum": BREAKDOWNS},
                    "include_months": INCLUDE_MONTHS_SCHEMA,
                    "filter_overrides": {
                        "type": "object",
                        "properties": FILTER_OVERRIDE_PROPS,
                    },
                    "visualization": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "bar_chart",
                            "column_chart",
                            "pie_chart",
                            "line_chart",
                            "table",
                            "metric",
                        ],
                        "description": (
                            "Chart type. Use auto when user does not specify — shows a picker "
                            "with pie, column, bar, and line. column_chart=vertical bars, "
                            "bar_chart=horizontal bars."
                        ),
                    },
                },
                "required": ["measure"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_analytics",
            "description": (
                "Generic analytics query for ticket data. Use for ALL metrics and breakdowns: "
                "ticket counts, approval/checker/approver times, send-backs, open-ticket aging, "
                "and trends over time. For follow-ups like 'by department', keep the same "
                "'measure' and add 'breakdown_by'. Dashboard filters apply automatically; "
                "use filter_overrides only when the user asks to narrow further (e.g. status=Closed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "measure": {
                        "type": "string",
                        "enum": MEASURES,
                        "description": (
                            "count=ticket volume; avg_*=average days; sendback_ticket_count=tickets "
                            "with L1 send-backs; total_sendbacks=sum of send-back events"
                        ),
                    },
                    "breakdown_by": {
                        "type": "string",
                        "enum": BREAKDOWNS,
                        "description": (
                            "Group results by this dimension. Use aging_bucket for open-ticket age "
                            "buckets; month/week for time trends. Omit for a single total."
                        ),
                    },
                    "secondary_breakdown_by": {
                        "type": "string",
                        "enum": BREAKDOWNS,
                        "description": (
                            "Optional nested breakdown, e.g. breakdown_by=department + "
                            "secondary_breakdown_by=aging_bucket for open-ticket aging by department."
                        ),
                    },
                    "include_months": INCLUDE_MONTHS_SCHEMA,
                    "filter_overrides": {
                        "type": "object",
                        "properties": FILTER_OVERRIDE_PROPS,
                        "description": "Optional filters on top of dashboard filters",
                    },
                },
                "required": ["measure"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tickets",
            "description": (
                "Search individual tickets by keyword across ticket number, division, "
                "department, status, and workflow. Use for specific ticket examples."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords, e.g. 'Supply Chain open'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max tickets to return (default 5, max 10)",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

MEASURE_LABELS = {
    "count": "ticket count",
    "avg_approval_time": "avg approval time",
    "avg_checker_time": "avg checker time",
    "avg_approver_time": "avg approver time",
    "avg_co_initiator_time": "avg co-initiator time",
    "sendback_ticket_count": "send-back tickets",
    "total_sendbacks": "send-back events",
}


def tool_label(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "update_dashboard_ui":
        return "Updating live dashboard"
    if tool_name == "add_analytics_surface":
        return "Adding new analytics chart"
    if tool_name == "search_tickets":
        return "Searching tickets"
    measure = arguments.get("measure", "count")
    breakdown = arguments.get("breakdown_by")
    measure_text = MEASURE_LABELS.get(measure, measure)
    if breakdown:
        return f"Fetching {measure_text} by {breakdown.replace('_', ' ')}"
    return f"Fetching {measure_text}"


TOOL_LABELS = {
    "update_dashboard_ui": "Updating dashboard",
    "add_analytics_surface": "Adding analytics view",
    "query_analytics": "Querying analytics",
    "search_tickets": "Searching tickets",
}


_MONTH_PATTERN = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.I,
)


WIDGET_PHRASES: list[tuple[str, str]] = [
    ("approval time by department", "approval_time_by_department"),
    ("approval time department", "approval_time_by_department"),
    ("department chart", "approval_time_by_department"),
    ("approval time by vertical", "approval_time_by_vertical"),
    ("approval time vertical", "approval_time_by_vertical"),
    ("vertical chart", "approval_time_by_vertical"),
    ("approval time explorer", "explorer_chart"),
    ("explorer chart", "explorer_chart"),
    ("configurable measure chart", "explorer_chart"),
    ("configurable measure", "explorer_chart"),
    ("open ticket aging", "open_ticket_aging"),
    ("ticket aging", "open_ticket_aging"),
    ("aging chart", "open_ticket_aging"),
    ("send-back trend", "admin_sendback_trend"),
    ("sendback trend", "admin_sendback_trend"),
    ("admin sendback", "admin_sendback_trend"),
    ("send back chart", "admin_sendback_trend"),
    ("approval time trend", "approval_time_trend"),
    ("approval time chart", "approval_time_trend"),
    ("approval chart", "approval_time_trend"),
    ("closure volume", "closure_volume"),
    ("closure month chart", "closure_volume"),
    ("closure month", "closure_volume"),
    ("closure chart", "closure_volume"),
    ("closed tickets chart", "closure_volume"),
    ("status mix", "status_mix"),
    ("status chart", "status_mix"),
    ("timing metrics", "timing_metrics"),
    ("status overview", "status_overview"),
]

_LABELS_PATTERN = re.compile(
    r"\b(add|show|enable)\b.*\b(number\s+)?l[ae]?b[ae]?ls?\b|\bdata labels\b|\bnumber labels\b|\blables\b",
    re.I,
)

_REMOVE_LABELS_PATTERN = re.compile(
    r"\b(remove|hide|delete|clear|turn off|disable)\b.*\b(number\s+)?l[ae]?b[ae]?ls?\b|"
    r"\b(remove|hide|delete|clear)\b.*\bdata labels\b|"
    r"\bno labels\b|\bwithout labels\b",
    re.I,
)

_FOLLOWUP_PREFIX = re.compile(r"^\s*(in|on|for)\s+", re.I)

_TAB_FOLLOWUP = re.compile(
    r"\b(?:"
    r"also|too|same|"
    r"this tab|here|"
    r"do (?:the )?same|"
    r"apply (?:that )?here"
    r")\b",
    re.I,
)

_ASSISTANT_LABELS_CONFIRM = re.compile(
    r"\b(data labels|display labels|labels on|labels to|added labels)\b",
    re.I,
)

EXECUTIVE_CHART_WIDGETS = {
    "approval_time_trend",
    "status_mix",
    "closure_volume",
    "status_overview",
    "timing_metrics",
}
OPERATIONAL_CHART_WIDGETS = {
    "open_ticket_aging",
    "admin_sendback_trend",
    "approval_time_by_vertical",
    "approval_time_by_department",
}
EXPLORER_CHART_WIDGETS = {"explorer_chart"}

EXECUTIVE_CONFIGURABLE_CHARTS = {
    "approval_time_trend",
    "status_mix",
    "closure_volume",
}

_CHANGE_INTENT = re.compile(
    r"\b(change|chnage|convert|switch|make|turn|show)\b",
    re.I,
)

_DATA_QUESTION = re.compile(
    r"\b("
    r"what|which|who|how many|how much|how long|"
    r"average|avg|total|count|tell me|explain|why|when|compare|list|"
    r"slowest|fastest|highest|lowest|most|least|best|worst|top|bottom|"
    r"maximum|minimum|max|min|rank|ranking"
    r")\b",
    re.I,
)

_UNSUPPORTED_UI_PATTERNS: list[tuple[str, str]] = [
    (r"\b3d\b", "3D charts aren't supported yet."),
    (
        r"\bstacked\b",
        "Stacked charts aren't supported on built-in dashboard widgets yet.",
    ),
    (
        r"\bdual[\s-]?axis\b|\btwo y[\s-]?axis",
        "Dual-axis charts aren't supported yet.",
    ),
    (
        r"\bcombined chart\b|\bmixed chart\b",
        "Combined chart types aren't supported — pick one: pie, column, bar, or line.",
    ),
]


def _extract_chart_types(text: str) -> list[str]:
    found: list[str] = []
    if re.search(r"\bpie\b|\bdonut\b|\bdoughnut\b", text):
        found.append("pie")
    if re.search(r"\bline\b", text):
        found.append("line")
    if re.search(r"\bcolumns?\b|\bvertical bar\b", text):
        found.append("column")
    if re.search(r"\bbar chart\b|\bhorizontal bar\b|\bas a bar\b", text) or re.search(
        r"\b(to|as)\s+bar\b", text
    ):
        found.append("bar")
    seen: set[str] = set()
    ordered: list[str] = []
    for chart_type in found:
        if chart_type not in seen:
            seen.add(chart_type)
            ordered.append(chart_type)
    return ordered


def _infer_tab_scope(
    prompt: str,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    text = (prompt or "").lower()
    if "operational" in text:
        return "operational"
    if "executive" in text:
        return "executive"
    if (
        "explorer" in text
        or "approvel" in text
        or "approval-time" in text
        or "approval time explorer" in text
    ):
        return "approval-time"
    active = (dashboard_context or {}).get("active_tab")
    return str(active) if active else None


def _widget_allowed_on_tab(widget_id: str, tab_scope: Optional[str]) -> bool:
    if not tab_scope:
        return True
    if tab_scope == "operational":
        return widget_id in OPERATIONAL_CHART_WIDGETS
    if tab_scope == "executive":
        return widget_id in EXECUTIVE_CHART_WIDGETS
    if tab_scope == "approval-time":
        return widget_id in EXPLORER_CHART_WIDGETS
    return True


def _is_tab_followup(prompt: str) -> bool:
    return bool(_TAB_FOLLOWUP.search(prompt or ""))


def _is_bare_followup_message(content: str) -> bool:
    if _LABELS_PATTERN.search(content) or _extract_chart_types(content):
        return False
    if _CHANGE_INTENT.search(content) and _extract_chart_types(content):
        return False
    if _FOLLOWUP_PREFIX.search(content):
        return True
    return _is_tab_followup(content)


def _widgets_for_tab(tab_scope: Optional[str]) -> list[str]:
    if tab_scope == "operational":
        return sorted(OPERATIONAL_CHART_WIDGETS)
    if tab_scope == "executive":
        return sorted(EXECUTIVE_CONFIGURABLE_CHARTS)
    if tab_scope == "approval-time":
        return sorted(EXPLORER_CHART_WIDGETS)
    return []


def _tab_named_in_prompt(prompt: str) -> bool:
    text = (prompt or "").lower()
    return any(
        token in text
        for token in (
            "operational",
            "executive",
            "explorer",
            "approval time explorer",
        )
    )


def _resolve_tab_followup_widgets(
    prompt: str,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> list[str]:
    tab_scope = _infer_tab_scope(prompt, dashboard_context)
    if not tab_scope:
        tab_scope = (dashboard_context or {}).get("active_tab")
    if not tab_scope:
        return []

    named = _match_widget_id(prompt, dashboard_context)
    if named:
        return [named]

    tab_widgets = _widgets_for_tab(tab_scope)
    if len(tab_widgets) == 1:
        return tab_widgets
    if tab_widgets and _is_tab_followup(prompt) and _tab_named_in_prompt(prompt):
        return tab_widgets
    return []


def _infer_pending_config_from_history(
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
    *,
    allow_widget_state: bool = False,
) -> Optional[dict[str, Any]]:
    if history:
        for msg in reversed(history[-8:]):
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user" and _is_bare_followup_message(content):
                continue
            if role == "assistant" and _ASSISTANT_LABELS_CONFIRM.search(content):
                return {"showDataLabels": True}
            if role != "user":
                continue
            text = content.lower()
            chart_types = _extract_chart_types(text)
            if len(chart_types) == 1 and _CHANGE_INTENT.search(text):
                return {"chartType": chart_types[0]}
            if _REMOVE_LABELS_PATTERN.search(text):
                return {"showDataLabels": False}
            if _LABELS_PATTERN.search(text):
                return {"showDataLabels": True}
            if re.search(r"\b(hide|remove|delete)\b", text) and re.search(
                r"\b(chart|graph|widget)\b", text
            ):
                return {"_action": "hide"}
            if re.search(r"\b(show|unhide|restore)\b", text):
                return {"_action": "show"}

    if allow_widget_state:
        widgets = (dashboard_context or {}).get("widgets") or []
        if any(
            isinstance(w, dict)
            and isinstance(w.get("props"), dict)
            and w["props"].get("showDataLabels")
            for w in widgets
        ):
            return {"showDataLabels": True}

    return None


def _resolve_widget_from_history(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    if history:
        for msg in reversed(history):
            widget_id = _match_widget_id(msg.get("content") or "", dashboard_context)
            if widget_id:
                return widget_id

    widgets = (dashboard_context or {}).get("widgets") or []
    configured = [
        w
        for w in widgets
        if isinstance(w, dict)
        and isinstance(w.get("props"), dict)
        and (w["props"].get("chartType") or w["props"].get("showDataLabels"))
    ]
    if configured:
        return str(configured[-1].get("id") or "")
    return None


def _is_ui_change_intent(text: str) -> bool:
    if _LABELS_PATTERN.search(text) or _REMOVE_LABELS_PATTERN.search(text):
        return True
    if _CHANGE_INTENT.search(text) and _extract_chart_types(text):
        return True
    if re.search(r"\b(change|convert|switch|make|turn)\b", text) and re.search(
        r"\b(chart|graph|widget|pie|bar|line|column|table)\b", text
    ):
        return True
    if re.search(r"\b(add|remove|hide|delete)\b", text) and re.search(
        r"\b(chart|graph|table|labels?|widget|surface)\b", text
    ):
        return True
    return False


def _is_data_question(prompt: str) -> bool:
    text = (prompt or "").lower()
    if _is_ui_change_intent(text):
        return False
    return bool(_DATA_QUESTION.search(text))


def is_likely_dashboard_ui_prompt(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> bool:
    text = (prompt or "").lower()
    if _is_data_question(prompt):
        return False
    if _match_widget_id(prompt, dashboard_context):
        return True
    if infer_remove_a2ui_from_prompt(prompt):
        return True
    if _LABELS_PATTERN.search(text) or _REMOVE_LABELS_PATTERN.search(text):
        return True
    if _CHANGE_INTENT.search(text) and _extract_chart_types(text):
        return True
    if re.search(r"\b(hide|show|remove|unhide|delete)\b", text) and re.search(
        r"\b(chart|graph|widget)\b", text
    ):
        return True
    if _is_tab_followup(text) and (
        _infer_pending_config_from_history(
            history, dashboard_context, allow_widget_state=True
        )
        or _resolve_tab_followup_widgets(prompt, dashboard_context)
    ):
        return True
    if history and _resolve_widget_from_history(prompt, history, dashboard_context):
        if _CHANGE_INTENT.search(text) or _LABELS_PATTERN.search(text):
            return True
    return False


def try_graceful_decline(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    text = (prompt or "").lower()

    if not is_likely_dashboard_ui_prompt(prompt, history, dashboard_context):
        return None

    chart_types = _extract_chart_types(text)
    if len(chart_types) > 1 and _CHANGE_INTENT.search(text):
        joined = ", ".join(chart_types)
        return (
            f"I can't apply **{joined}** together on one chart. "
            "Pick a single type: **pie**, **column**, **bar**, or **line**."
        )

    for pattern, message in _UNSUPPORTED_UI_PATTERNS:
        if re.search(pattern, text, re.I):
            return message

    needs_widget = bool(
        _CHANGE_INTENT.search(text) and chart_types
    ) or _LABELS_PATTERN.search(text) or _REMOVE_LABELS_PATTERN.search(text) or re.search(
        r"\b(hide|show|remove|unhide|delete)\b", text
    )
    if needs_widget:
        widget_id = _match_widget_id(prompt, dashboard_context) or _resolve_widget_from_history(
            prompt, history, dashboard_context
        )
        if not widget_id:
            tab_scope = _infer_tab_scope(prompt, dashboard_context)
            if tab_scope == "operational" and "approval" in text:
                return (
                    "Which operational chart do you mean — "
                    "*approval time by vertical* or *approval time by department*?"
                )
            return (
                "I'm not sure which chart you mean. "
                "Please name it — e.g. *approval time trend* or *closure volume*."
            )

    return None


def try_dashboard_unfulfilled_message(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    if not is_likely_dashboard_ui_prompt(prompt, history, dashboard_context):
        return None

    actions = infer_widget_actions_from_prompt(prompt, history, dashboard_context)
    if actions or infer_remove_a2ui_from_prompt(prompt):
        return None

    return (
        "I can't do that with the dashboard right now. "
        "Try naming a specific chart and one change — e.g. "
        "*change approval time trend to pie chart* or *add labels on closure volume*."
    )


def _match_widget_id(
    prompt: str,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    if _is_data_question(prompt):
        return None

    text = (prompt or "").lower()
    tab_scope = _infer_tab_scope(prompt, dashboard_context)

    for phrase, widget_id in WIDGET_PHRASES:
        if phrase in text and _widget_allowed_on_tab(widget_id, tab_scope):
            return widget_id

    if "closure" in text and ("month" in text or "volume" in text or "vloume" in text):
        return "closure_volume"
    if "explorer" in text or "configurable measure" in text:
        return "explorer_chart"
    if ("aging" in text or "aged" in text) and ("open" in text or "ticket" in text):
        return "open_ticket_aging"
    if "send" in text and "back" in text:
        return "admin_sendback_trend"
    if "department" in text and "approval" in text:
        return "approval_time_by_department"
    if "vertical" in text and "approval" in text:
        return "approval_time_by_vertical"
    if "approval" in text and re.search(r"\btrend\b|\btrned\b|\btrnd\b", text):
        if tab_scope != "operational":
            return "approval_time_trend"
    if ("approval time" in text or "approval chart" in text) and tab_scope == "operational":
        return None
    return None


def _configure_actions_for_widget(
    widget_id: str,
    props: dict[str, Any],
) -> list[dict[str, Any]]:
    if not props:
        return []
    return [{"action": "configure", "widget_id": widget_id, "props": props}]


def _build_widget_actions(
    prompt: str,
    widget_ids: list[str],
    history: Optional[list[dict[str, str]]],
    dashboard_context: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not widget_ids:
        return []

    text = (prompt or "").lower()
    is_followup = bool(_FOLLOWUP_PREFIX.search(prompt or "")) or _is_tab_followup(prompt)

    if _REMOVE_LABELS_PATTERN.search(text):
        actions: list[dict[str, Any]] = []
        for widget_id in widget_ids:
            actions.extend(
                _configure_actions_for_widget(widget_id, {"showDataLabels": False})
            )
        return actions

    if re.search(r"\b(remove|hide|delete)\b", text) and re.search(
        r"\b(chart|graph|widget|section)\b", text
    ):
        if not re.search(r"\blabels?\b", text):
            return [{"action": "hide", "widget_id": widget_ids[0]}]

    if re.search(r"\b(show|unhide|restore|bring back)\b", text) and not re.search(
        r"\blabels?\b", text
    ):
        return [{"action": "show", "widget_id": widget_ids[0]}]

    chart_types = _extract_chart_types(text)
    if len(chart_types) > 1 and _CHANGE_INTENT.search(text):
        return []

    chart_type = chart_types[0] if len(chart_types) == 1 else None
    actions: list[dict[str, Any]] = []

    if chart_type and (_CHANGE_INTENT.search(text) or (is_followup and chart_type)):
        actions.extend(
            _configure_actions_for_widget(
                widget_ids[0], {"chartType": chart_type}
            )
        )

    if _LABELS_PATTERN.search(text):
        for widget_id in widget_ids:
            actions.extend(
                _configure_actions_for_widget(
                    widget_id, {"showDataLabels": True}
                )
            )
        return actions

    if not actions:
        pending = _infer_pending_config_from_history(
            history,
            dashboard_context,
            allow_widget_state=is_followup,
        )
        if pending:
            pending = dict(pending)
            pending_action = pending.pop("_action", None)
            if is_followup and "chartType" in pending and not chart_types:
                pending.pop("chartType", None)
            if pending_action in {"hide", "show"}:
                return [{"action": pending_action, "widget_id": widget_ids[0]}]
            if pending:
                for widget_id in widget_ids:
                    actions.extend(_configure_actions_for_widget(widget_id, pending))

    return actions


def infer_widget_actions_from_prompt(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    if infer_remove_a2ui_from_prompt(prompt):
        return []
    if is_add_analytics_prompt(prompt, history):
        return []

    widget_ids: list[str] = []
    named = _match_widget_id(prompt, dashboard_context)
    if named:
        widget_ids = [named]
    elif _is_tab_followup(prompt):
        widget_ids = _resolve_tab_followup_widgets(prompt, dashboard_context)
        if not widget_ids:
            tab_scope = (dashboard_context or {}).get("active_tab")
            tab_widgets = _widgets_for_tab(tab_scope)
            if len(tab_widgets) == 1:
                widget_ids = tab_widgets
    else:
        resolved = _resolve_widget_from_history(prompt, history, dashboard_context)
        if resolved:
            widget_ids = [resolved]

    return _build_widget_actions(prompt, widget_ids, history, dashboard_context)


def infer_visualization_from_prompt(prompt: str) -> Optional[str]:
    text = (prompt or "").lower()
    if re.search(r"\btable\b", text):
        return "table"
    if re.search(r"\bpie\b|\bdonut\b|\bdoughnut\b", text):
        return "pie_chart"
    if re.search(r"\bline chart\b|\bline graph\b|\btrend line\b", text):
        return "line_chart"
    if re.search(r"\bcolumn chart\b|\bcolumn graph\b|\bvertical bar\b", text):
        return "column_chart"
    if re.search(
        r"\bbar chart\b|\bbar graph\b|\bhistogram\b|\bhorizontal bar\b|\bas a bar\b", text
    ):
        return "bar_chart"
    return None


_ADD_SURFACE_INTENT = re.compile(
    r"\b(add|create|show|make|build|new|generate|genrate)\b.*\b(chart|graph|table|view)\b|"
    r"\b(chart|graph|table)\s+for\b|"
    r"\b(bar|column|pie|line)\s+chart\b.*\b(tickets?|by)\b",
    re.I,
)

_BREAKDOWN_KEYWORDS: list[tuple[str, str]] = [
    ("department", "department"),
    ("vertical", "vertical"),
    ("division", "division"),
    ("ticket type", "ticket_type"),
    ("ticket_type", "ticket_type"),
    ("status", "status"),
    ("workflow", "workflow"),
    ("month", "month"),
    ("week", "week"),
]


def _is_add_analytics_followup(prompt: str) -> bool:
    text = (prompt or "").lower()
    if not re.search(r"\b(also|too)\b", text) and not _FOLLOWUP_PREFIX.search(text):
        return False
    return _infer_breakdown_from_prompt(text) is not None


def is_add_analytics_prompt(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
) -> bool:
    text = (prompt or "").lower()
    if _LABELS_PATTERN.search(text) or _REMOVE_LABELS_PATTERN.search(text):
        return False
    if _CHANGE_INTENT.search(text) and _extract_chart_types(text):
        if not re.search(
            r"\b(add|create|make|build|new|generate|genrate)\b.*\b(chart|graph|table|view)\b",
            text,
        ):
            return False
    if _ADD_SURFACE_INTENT.search(text):
        return True
    if re.search(r"\btable\b", text) and (
        re.search(r"\bapproval time\b", text) or _infer_breakdown_from_prompt(text)
    ):
        return True
    if re.search(r"\b(add|create|show|make|build|new|generate|genrate)\b", text) and (
        infer_months_from_prompt(prompt) or _infer_breakdown_from_prompt(text)
    ):
        return True
    if re.search(r"\b(bar|column|pie|line)\s+chart\b", text) and re.search(
        r"\btickets?\b", text
    ):
        return True
    if _is_add_analytics_followup(prompt) and history:
        return infer_add_analytics_from_prompt(prompt, history) is not None
    return False


def _infer_breakdown_from_prompt(
    text: str,
    *,
    approval_time: bool = False,
) -> Optional[str]:
    """Map natural language to analytics breakdown_by.

    Operational dashboard labels the department chart "Approval Time by Division –
    Department". For approval-time metrics, "by division" means department breakdown.
    """
    lowered = (text or "").lower()
    ordered_phrases = [
        ("by month", "month"),
        ("per month", "month"),
        ("monthly", "month"),
        ("by department", "department"),
        ("by vertical", "vertical"),
        ("by division", "division"),
        ("by status", "status"),
        ("by workflow", "workflow"),
        ("ticket type", "ticket_type"),
    ]
    for phrase, field in ordered_phrases:
        if phrase in lowered:
            if approval_time and field == "division":
                return "department"
            return field
    for phrase, field in _BREAKDOWN_KEYWORDS:
        if phrase in lowered:
            if approval_time and field == "division":
                return "department"
            return field
    return None


def infer_status_from_prompt(prompt: str) -> Optional[str]:
    text = (prompt or "").lower()
    if re.search(r"\bopen tickets?\b|\bopen ticket\b|\btickets?\s+open\b", text):
        return "Open"
    if re.search(r"\brejected tickets?\b|\btickets?\s+rejected\b", text):
        return "Rejected"
    if re.search(
        r"\b(closure|closing|closed tickets?|closed ticket|ticket closures?)\b",
        text,
    ):
        return "Closed"
    return None


def _infer_add_analytics_followup_from_history(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
) -> Optional[dict[str, Any]]:
    if not history or not _is_add_analytics_followup(prompt):
        return None

    prompt_key = (prompt or "").strip().lower()
    prior_measure: Optional[str] = None
    for msg in reversed(history[-8:]):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if content.strip().lower() == prompt_key:
            continue
        prior = _build_add_analytics_args(content)
        if not prior:
            continue
        prior_measure = str(prior.get("measure") or "")
        breakdown_by = _infer_breakdown_from_prompt(
            prompt,
            approval_time=prior_measure == "avg_approval_time",
        )
        if not breakdown_by:
            return None
        args = dict(prior)
        args["breakdown_by"] = breakdown_by
        viz = infer_visualization_from_prompt(prompt)
        if viz:
            args["visualization"] = viz
        args.pop("include_months", None)
        overrides = dict(args.get("filter_overrides") or {})
        overrides.pop("months", None)
        if overrides:
            args["filter_overrides"] = overrides
        elif "filter_overrides" in args:
            del args["filter_overrides"]
        return args
    return None


def _build_add_analytics_args(prompt: str) -> Optional[dict[str, Any]]:
    text = (prompt or "").lower()
    months = infer_months_from_prompt(prompt)
    is_approval_time = bool(re.search(r"\bapproval time\b", text))
    breakdown_by = _infer_breakdown_from_prompt(text, approval_time=is_approval_time)
    has_surface_intent = bool(_ADD_SURFACE_INTENT.search(text))
    has_month_add = bool(
        re.search(r"\b(add|create|show|make|build|new|generate|genrate)\b", text) and months
    )
    has_ticket_chart = bool(
        re.search(r"\b(bar|column|pie|line)\s+chart\b", text)
        and re.search(r"\btickets?\b", text)
    )
    has_table_analytics = bool(
        re.search(r"\btable\b", text)
        and (re.search(r"\bapproval time\b", text) or breakdown_by)
    )
    if not (
        has_surface_intent
        or has_month_add
        or has_ticket_chart
        or has_table_analytics
    ):
        return None

    visualization = infer_visualization_from_prompt(prompt) or "auto"

    if months and not breakdown_by:
        breakdown_by = "month"

    if not months and not breakdown_by and visualization == "auto":
        return None

    measure = "count"
    if is_approval_time:
        measure = "avg_approval_time"

    args: dict[str, Any] = {
        "measure": measure,
        "visualization": visualization,
    }
    if breakdown_by:
        args["breakdown_by"] = breakdown_by
    if months:
        args["include_months"] = months

    overrides: dict[str, Any] = {}
    status = infer_status_from_prompt(prompt)
    if status:
        overrides["status"] = status
    elif measure == "avg_approval_time":
        overrides["status"] = "Closed"
    elif (
        breakdown_by == "month"
        and measure == "count"
        and re.search(r"\b(closure|closed|closing)\b", text)
    ):
        overrides["status"] = "Closed"
    if overrides:
        args["filter_overrides"] = overrides

    return args


def infer_add_analytics_from_prompt(
    prompt: str,
    history: Optional[list[dict[str, str]]] = None,
) -> Optional[dict[str, Any]]:
    followup = _infer_add_analytics_followup_from_history(prompt, history)
    if followup:
        return followup
    return _build_add_analytics_args(prompt)


def merge_add_analytics_args(
    prompt: str,
    args: dict[str, Any],
    history: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Prefer prompt-inferred measure/breakdown/visualization over mistaken LLM args."""
    inferred = infer_add_analytics_from_prompt(prompt, history)
    if not inferred:
        return args

    merged = dict(args)
    for key in ("measure", "breakdown_by", "visualization"):
        if inferred.get(key):
            merged[key] = inferred[key]

    if infer_months_from_prompt(prompt):
        if inferred.get("include_months"):
            merged["include_months"] = inferred["include_months"]
    else:
        merged.pop("include_months", None)
        filter_overrides = dict(merged.get("filter_overrides") or {})
        filter_overrides.pop("months", None)
        if filter_overrides:
            merged["filter_overrides"] = filter_overrides
        else:
            merged.pop("filter_overrides", None)

    if inferred.get("filter_overrides"):
        merged["filter_overrides"] = {
            **(merged.get("filter_overrides") or {}),
            **inferred["filter_overrides"],
        }

    if merged.get("measure") == "avg_approval_time" and merged.get("breakdown_by") == "division":
        merged["breakdown_by"] = "department"

    return merged


def infer_remove_a2ui_from_prompt(prompt: str) -> bool:
    text = (prompt or "").lower()
    if infer_bulk_remove_actions(prompt) is not None:
        return True
    return bool(
        re.search(
            r"\bremove\b.*\b(this|added|ai|a2ui|new|agent)\b|\bremove this chart\b|\bdelete this chart\b",
            text,
        )
    )


def infer_bulk_remove_actions(
    prompt: str,
    dashboard_context: Optional[dict[str, Any]] = None,
) -> Optional[list[dict[str, Any]]]:
    text = (prompt or "").lower()
    if not re.search(r"\b(remove|delete|clear)\b", text):
        return None
    if not re.search(r"\ball\b", text) and not re.search(
        r"\b(every|each)\b.*\b(chart|graph)\b", text
    ):
        return None
    if not re.search(r"\b(charts?|graphs?|surfaces?|added|ai)\b", text):
        return None

    surface_ids = (dashboard_context or {}).get("surface_ids") or []
    return [{"action": "remove_surface", "surface_id": sid} for sid in surface_ids]


def infer_closure_status_from_prompt(prompt: str) -> Optional[str]:
    status = infer_status_from_prompt(prompt)
    return status if status == "Closed" else None


def infer_months_from_prompt(prompt: str) -> list[str]:
    matches = _MONTH_PATTERN.findall(prompt or "")
    if not matches:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in matches:
        token = match.lower()
        if token in seen:
            continue
        seen.add(token)
        ordered.append(match.capitalize() if len(match) <= 3 else match.title())
    return ordered


def _compose_filter_overrides(arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
    overrides = dict(arguments.get("filter_overrides") or {})
    include_months = arguments.get("include_months")
    if include_months:
        existing = overrides.get("months") or []
        if not isinstance(existing, list):
            existing = [existing]
        overrides["months"] = [*existing, *include_months]
    return overrides or None


def _search_tickets(user_id: str, filters: AnalyticsFilters, query: str, limit: int = 5) -> list[dict]:
    limit = max(1, min(limit, 10))
    match = build_match_filter(user_id, filters)
    keywords = [part for part in re.split(r"\s+", query.strip()) if part]

    if keywords:
        or_clauses = []
        for keyword in keywords:
            pattern = {"$regex": re.escape(keyword), "$options": "i"}
            or_clauses.append({"ticket_number": pattern})
            or_clauses.append({"division": pattern})
            or_clauses.append({"department": pattern})
            or_clauses.append({"status": pattern})
            or_clauses.append({"workflow": pattern})
            or_clauses.append({"vertical": pattern})
        match["$or"] = or_clauses

    collection = get_database().tickets
    cursor = collection.find(match).sort("created_at", -1).limit(limit)
    results = []
    for doc in cursor:
        results.append(
            {
                "ticket_number": doc.get("ticket_number"),
                "status": doc.get("status"),
                "division": doc.get("division"),
                "department": doc.get("department"),
                "vertical": doc.get("vertical"),
                "approval_time_days": doc.get("approval_time_days"),
                "admin_l1_sendback_count": doc.get("admin_l1_sendback_count"),
                "created_at": str(doc.get("created_at")),
            }
        )
    return results


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    user_id: str,
    filters: AnalyticsFilters,
    explorer_defaults: dict[str, str],
) -> str:
    del explorer_defaults  # kept for call-site compatibility

    if name == "update_dashboard_ui":
        raw_actions = arguments.get("actions") or []
        validated = []
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            parsed = validate_action(item)
            if parsed:
                validated.append(parsed)
        return json.dumps({"ok": True, "actions": validated, "applied": len(validated)})

    if name == "add_analytics_surface":
        measure = str(arguments.get("measure") or "count")
        breakdown_by = arguments.get("breakdown_by")
        if measure == "avg_approval_time" and breakdown_by == "division":
            breakdown_by = "department"
        secondary = arguments.get("secondary_breakdown_by")
        overrides = _compose_filter_overrides(arguments)
        visualization = str(arguments.get("visualization") or "auto")
        query_limit = 50
        if measure == "avg_approval_time" and breakdown_by == "department":
            query_limit = 8
        data = analytics_service.query_analytics(
            user_id,
            filters,
            measure=measure,
            breakdown_by=breakdown_by,
            secondary_breakdown_by=secondary,
            filter_overrides=overrides,
            limit=query_limit,
        )
        payload = data.model_dump()
        surface_id, messages = build_analytics_surface_messages(
            payload,
            prefer=visualization,
        )
        return json.dumps(
            {
                "surface_id": surface_id,
                "a2ui_messages": messages,
                "analytics": payload,
            }
        )

    if name == "query_analytics":
        measure = str(arguments.get("measure") or "count")
        breakdown_by = arguments.get("breakdown_by")
        secondary = arguments.get("secondary_breakdown_by")
        overrides = _compose_filter_overrides(arguments)
        data = analytics_service.query_analytics(
            user_id,
            filters,
            measure=measure,
            breakdown_by=breakdown_by,
            secondary_breakdown_by=secondary,
            filter_overrides=overrides,
        )
        return data.model_dump_json()

    if name == "search_tickets":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return json.dumps({"tickets": [], "message": "Empty search query"})
        limit = int(arguments.get("limit") or 5)
        tickets = _search_tickets(user_id, filters, query, limit=limit)
        payload = {"query": query, "count": len(tickets), "tickets": tickets}
        if tickets:
            surface_id, messages = build_ticket_list_surface(payload)
            payload["surface_id"] = surface_id
            payload["a2ui_messages"] = messages
        return json.dumps(payload)

    return json.dumps({"error": f"Unknown tool: {name}"})


def parse_user_id(user_id: str | uuid.UUID) -> str:
    return str(user_id)

"""Fixed dashboard widget catalog for update_dashboard_ui."""

from __future__ import annotations

from typing import Any

# Executive tab widgets
EXECUTIVE_WIDGETS: dict[str, dict[str, Any]] = {
    "status_overview": {
        "title": "Status overview",
        "tab": "executive",
        "configurable": [],
    },
    "timing_metrics": {
        "title": "Timing metrics",
        "tab": "executive",
        "configurable": [],
    },
    "approval_time_trend": {
        "title": "Approval Time Trend",
        "tab": "executive",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
    "status_mix": {
        "title": "Status Mix",
        "tab": "executive",
        "configurable": ["showLegend", "chartType"],
    },
    "closure_volume": {
        "title": "Closure Volume by Month",
        "tab": "executive",
        "configurable": ["showDataLabels", "chartType"],
    },
}

OPERATIONAL_WIDGETS: dict[str, dict[str, Any]] = {
    "open_ticket_aging": {
        "title": "Open Ticket Aging",
        "tab": "operational",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
    "admin_sendback_trend": {
        "title": "Admin Send-back Trend",
        "tab": "operational",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
    "approval_time_by_vertical": {
        "title": "Approval Time by Vertical",
        "tab": "operational",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
    "approval_time_by_department": {
        "title": "Approval Time by Department",
        "tab": "operational",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
}

EXPLORER_WIDGETS: dict[str, dict[str, Any]] = {
    "explorer_chart": {
        "title": "Approval Time Explorer",
        "tab": "approval-time",
        "configurable": ["showDataLabels", "showGrid", "chartType"],
    },
}

ALL_WIDGETS: dict[str, dict[str, Any]] = {
    **EXECUTIVE_WIDGETS,
    **OPERATIONAL_WIDGETS,
    **EXPLORER_WIDGETS,
}

ALLOWED_ACTIONS = {"hide", "show", "configure", "navigate", "remove_surface"}


def get_widget_title(widget_id: str) -> str | None:
    meta = ALL_WIDGETS.get(widget_id)
    return meta["title"] if meta else None


def get_catalog_for_client(active_tab: str | None = None) -> dict[str, Any]:
    del active_tab  # full catalog is sent so the agent can mutate any widget
    widgets = []
    for widget_id, meta in ALL_WIDGETS.items():
        widgets.append(
            {
                "id": widget_id,
                "title": meta["title"],
                "tab": meta["tab"],
                "configurable": meta.get("configurable", []),
            }
        )
    return {
        "allowed_actions": sorted(ALLOWED_ACTIONS),
        "widgets": widgets,
        "addable_surface_types": [
            "chart_selector",
            "bar_chart",
            "column_chart",
            "pie_chart",
            "line_chart",
            "table",
            "metric",
        ],
    }


def validate_action(action: dict[str, Any]) -> dict[str, Any] | None:
    action_type = action.get("action")
    if action_type not in ALLOWED_ACTIONS:
        return None

    if action_type == "navigate":
        tab = action.get("tab")
        if tab not in {"executive", "operational", "approval-time", "ai-insights"}:
            return None
        return {"action": "navigate", "tab": tab}

    if action_type == "remove_surface":
        surface_id = action.get("surface_id")
        if not surface_id or not isinstance(surface_id, str):
            return None
        return {"action": "remove_surface", "surface_id": surface_id}

    widget_id = action.get("widget_id")
    if not widget_id or widget_id not in ALL_WIDGETS:
        return None

    if action_type in {"hide", "show"}:
        return {"action": action_type, "widget_id": widget_id}

    if action_type == "configure":
        props = action.get("props")
        if not isinstance(props, dict):
            return None
        allowed = set(ALL_WIDGETS[widget_id].get("configurable", []))
        filtered: dict[str, Any] = {}
        for key, value in props.items():
            if key not in allowed:
                continue
            if key == "chartType" and value not in {"pie", "column", "bar", "line"}:
                continue
            filtered[key] = value
        if not filtered:
            return None
        return {"action": "configure", "widget_id": widget_id, "props": filtered}

    return None

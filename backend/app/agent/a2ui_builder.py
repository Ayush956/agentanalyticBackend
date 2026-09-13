"""Build A2UI v1.0 message streams for new analytics surfaces."""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

ANALYTICS_CATALOG_ID = "https://agentanalytics.local/catalogs/analytics/v1"

MEASURE_TITLES = {
    "count": "Ticket count",
    "avg_approval_time": "Average approval time",
    "avg_checker_time": "Average checker time",
    "avg_approver_time": "Average approver time",
    "avg_co_initiator_time": "Average co-initiator time",
    "sendback_ticket_count": "Send-back tickets",
    "total_sendbacks": "Send-back events",
}


def _surface_id(prefix: str = "agent") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _format_month_label(month_key: str) -> str:
    try:
        year, month = month_key.split("-", 1)
        names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        idx = int(month) - 1
        if 0 <= idx < 12:
            return f"{names[idx]} '{year[2:]}"
    except (ValueError, IndexError):
        pass
    return month_key


def _title_from_analytics(payload: dict[str, Any]) -> str:
    measure = payload.get("measure", "count")
    breakdown = payload.get("breakdown_by")
    applied = payload.get("filters_applied") or {}
    status = applied.get("status")
    months = applied.get("months") or []
    if measure == "avg_approval_time" and breakdown == "vertical":
        base = "Approval time by vertical"
    elif measure == "avg_approval_time" and breakdown == "department":
        base = "Approval time by division"
    elif measure == "count" and breakdown == "month":
        if status == "Closed":
            base = "Closure volume"
        elif status == "Open":
            base = "Open tickets"
        elif status == "Rejected":
            base = "Rejected tickets"
        else:
            base = MEASURE_TITLES.get(measure, "Analytics")
    elif measure == "count" and breakdown == "status":
        base = "Tickets by status"
    else:
        base = MEASURE_TITLES.get(measure, "Analytics")
        if status:
            base = f"{base} ({status})"

    if months:
        month_labels = ", ".join(_format_month_label(str(item)) for item in months)
        base = f"{base} — {month_labels}"
    elif breakdown and not (
        measure == "avg_approval_time" and breakdown in {"vertical", "department"}
    ):
        base = f"{base} by {str(breakdown).replace('_', ' ')}"
    return base


def build_analytics_surface_messages(
    analytics_payload: dict[str, Any],
    surface_id: Optional[str] = None,
    prefer: str = "auto",
) -> tuple[str, list[dict[str, Any]]]:
    """Return (surface_id, A2UI messages) for an analytics query result."""
    surface_id = surface_id or _surface_id()
    title = _title_from_analytics(analytics_payload)
    measure = analytics_payload.get("measure", "count")
    unit = analytics_payload.get("unit") or ("days" if measure.startswith("avg_") else "tickets")
    breakdown = analytics_payload.get("breakdown_by")
    rows = analytics_payload.get("data") or []
    total_value = analytics_payload.get("total_value", 0)

    explicit_chart_types = {"bar_chart", "pie_chart", "line_chart", "column_chart"}
    use_metric = prefer == "metric" or (prefer == "auto" and not breakdown)
    use_chart = prefer in explicit_chart_types or (
        prefer == "auto"
        and breakdown
        and breakdown != "aging_bucket"
        and len(rows) > 0
    )
    use_table = prefer == "table" or (prefer == "auto" and breakdown and not use_chart)

    component_type = "AnalyticsChartSelector"
    if use_table:
        component_type = "AnalyticsDataTable"
    elif use_metric:
        component_type = "AnalyticsMetric"
    elif use_chart:
        if prefer == "pie_chart":
            component_type = "AnalyticsPieChart"
        elif prefer == "line_chart":
            component_type = "AnalyticsLineChart"
        elif prefer == "column_chart":
            component_type = "AnalyticsColumnChart"
        elif prefer == "bar_chart":
            component_type = "AnalyticsBarChart"
        elif prefer == "auto":
            component_type = "AnalyticsChartSelector"

    table_rows = []
    chart_rows = []
    for item in rows:
        label = str(item.get("label", "Unknown"))
        value = item.get("value", item.get("ticket_count", 0))
        pct = item.get("percentage")
        chart_rows.append({"label": label, "value": float(value)})
        if measure.startswith("avg_"):
            table_rows.append([label, f"{value} {unit}", str(item.get("ticket_count", ""))])
        else:
            pct_text = f"{pct}%" if pct is not None else ""
            table_rows.append([label, str(int(value) if float(value).is_integer() else value), pct_text])

    if measure.startswith("avg_") and breakdown:
        if breakdown == "department":
            category_label = "Department"
        else:
            category_label = str(breakdown).replace("_", " ").title()
        table_columns = [category_label, f"Avg ({unit})", "Tickets"]
    else:
        table_columns = ["Category", "Value", "Tickets / %"]

    data_model: dict[str, Any] = {
        "title": title,
        "totalValue": total_value,
        "unit": unit,
        "chartData": chart_rows,
        "tableColumns": table_columns,
        "tableRows": table_rows,
        "metricLabel": title,
        "metricValue": str(int(total_value) if float(total_value).is_integer() else total_value),
        "metricUnit": unit,
    }

    chart_components = {
        "AnalyticsBarChart",
        "AnalyticsColumnChart",
        "AnalyticsPieChart",
        "AnalyticsLineChart",
        "AnalyticsChartSelector",
    }
    if component_type in chart_components:
        root_component = {
            "id": "root",
            "component": component_type,
            "title": {"path": "/title"},
            "data": {"path": "/chartData"},
            "unit": {"path": "/unit"},
        }
    elif component_type == "AnalyticsDataTable":
        root_component = {
            "id": "root",
            "component": "AnalyticsDataTable",
            "title": {"path": "/title"},
            "columns": {"path": "/tableColumns"},
            "rows": {"path": "/tableRows"},
        }
    else:
        root_component = {
            "id": "root",
            "component": "AnalyticsMetric",
            "label": {"path": "/metricLabel"},
            "value": {"path": "/metricValue"},
            "unit": {"path": "/metricUnit"},
        }

    messages: list[dict[str, Any]] = [
        {
            "version": "v1.0",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": ANALYTICS_CATALOG_ID,
                "sendDataModel": True,
            },
        },
        {
            "version": "v1.0",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [root_component],
            },
        },
        {
            "version": "v1.0",
            "updateDataModel": {
                "surfaceId": surface_id,
                "value": data_model,
            },
        },
    ]
    return surface_id, messages


def build_ticket_list_surface(tickets_payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    surface_id = _surface_id("tickets")
    query = tickets_payload.get("query", "Tickets")
    tickets = tickets_payload.get("tickets") or []
    rows = []
    for ticket in tickets:
        rows.append(
            [
                str(ticket.get("ticket_number", "")),
                str(ticket.get("status", "")),
                str(ticket.get("department", "")),
                str(ticket.get("approval_time_days", "")),
            ]
        )

    messages = [
        {
            "version": "v1.0",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": ANALYTICS_CATALOG_ID,
                "sendDataModel": True,
            },
        },
        {
            "version": "v1.0",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [
                    {
                        "id": "root",
                        "component": "AnalyticsDataTable",
                        "title": f"Search: {query}",
                        "columns": ["Ticket", "Status", "Department", "Approval days"],
                        "rows": {"path": "/rows"},
                    },
                ],
            },
        },
        {
            "version": "v1.0",
            "updateDataModel": {"surfaceId": surface_id, "value": {"rows": rows}},
        },
    ]
    return surface_id, messages


def messages_to_json(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages)

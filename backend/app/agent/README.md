# Agent Architecture

## Hybrid UI model

This copilot uses two complementary patterns:

### 1. Dashboard UI commands (`update_dashboard_ui`)

For **existing** dashboard widgets (Executive, Operational, Explorer):

- `hide` / `show` — remove or restore built-in charts
- `configure` — change props (e.g. `showDataLabels` on `approval_time_trend`)
- `navigate` — switch analytics tab
- `remove_surface` — remove an agent-added A2UI block

Transport: WebSocket `{ type: "dashboard_ui", actions: [...] }`

### 2. A2UI surfaces (`add_analytics_surface`)

For **new** charts, tables, and metrics with live data:

- Agent calls `add_analytics_surface` (or `search_tickets` with results)
- Backend builds [A2UI v1.0](https://a2ui.org/) message streams via `a2ui_builder.py`
- Frontend renders with `@a2ui/react` + custom analytics catalog (`AnalyticsBarChart`, `AnalyticsDataTable`, `AnalyticsMetric`)

Transport: WebSocket `{ type: "a2ui", surface_id, messages }`

### 3. Data-only questions (`query_analytics`)

When the user asks a question without requesting UI changes, use `query_analytics` and answer in chat text.

## Tools

| Tool | Purpose |
|------|---------|
| `update_dashboard_ui` | Mutate live dashboard widgets |
| `add_analytics_surface` | Add new A2UI surface with analytics data |
| `query_analytics` | Fetch metrics (no UI change) |
| `search_tickets` | Keyword search; may emit A2UI ticket table |

## Routing

Every user message goes to the **LLM first**. The model picks a tool; the backend executes
it (WebSocket `dashboard_ui` / `a2ui` for UI, Mongo for data). Regex helpers in `tools.py`
only **enrich** tool arguments after the LLM call (e.g. month/status inference), not block
the LLM.

## Files

- `runner.py` — LLM tool-calling loop
- `tools.py` — tool definitions
- `widget_catalog.py` — fixed widget IDs and allowed actions
- `a2ui_builder.py` — analytics JSON → A2UI messages
- `prompts.py` — agent routing rules

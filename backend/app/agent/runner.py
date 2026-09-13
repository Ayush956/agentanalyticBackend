from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Optional

from app.agent.llm_client import AgentLLM, AllProvidersExhaustedError, friendly_llm_error
from app.agent.prompts import AGENT_SYSTEM_PROMPT
from app.agent.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    infer_add_analytics_from_prompt,
    merge_add_analytics_args,
    infer_bulk_remove_actions,
    infer_closure_status_from_prompt,
    infer_status_from_prompt,
    infer_months_from_prompt,
    infer_remove_a2ui_from_prompt,
    infer_visualization_from_prompt,
    infer_widget_actions_from_prompt,
    try_dashboard_unfulfilled_message,
    try_graceful_decline,
)
from app.agent.widget_catalog import get_widget_title
from app.config import get_settings
from app.schemas.analytics import AnalyticsFilters

MAX_TOOL_ROUNDS = 5
FALLBACK_MESSAGE = (
    "I can't do that reliably right now. "
    "For dashboard changes, name a chart and pick one type (pie, column, bar, or line). "
    "For data questions, ask about counts, averages, or trends."
)
TIMEOUT_MESSAGE = (
    "That request took too long. I can't complete it right now — "
    "try a simpler phrasing or one chart change at a time."
)
OnToolCall = Callable[[str, Optional[dict[str, Any]]], Awaitable[None]]
OnDashboardUI = Callable[[list[dict[str, Any]]], Awaitable[None]]
OnA2UI = Callable[[str, list[dict[str, Any]], Optional[str]], Awaitable[None]]


def _build_context_message(
    filters: AnalyticsFilters,
    active_tab: Optional[str],
    explorer_config: dict[str, str],
    dashboard_context: Optional[dict[str, Any]] = None,
) -> str:
    from app.agent.widget_catalog import get_catalog_for_client

    payload = {
        "active_tab": active_tab,
        "filters": filters.model_dump(exclude_none=True),
        "explorer_config": explorer_config,
        "widget_catalog": get_catalog_for_client(active_tab),
        "dashboard_state": dashboard_context or {},
    }
    return f"Dashboard context:\n{json.dumps(payload, indent=2)}"


def _tool_message_content(raw: str, tool_name: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if tool_name == "add_analytics_surface" and isinstance(parsed, dict):
        analytics = parsed.get("analytics") or {}
        from app.agent.a2ui_builder import _title_from_analytics

        title = _title_from_analytics(analytics)
        return json.dumps(
            {
                "ok": True,
                "surface_added": True,
                "surface_id": parsed.get("surface_id"),
                "title": title,
                "total_value": analytics.get("total_value"),
                "reply_hint": (
                    f"Confirm briefly that '{title}' was added to the dashboard "
                    "(scroll to 'Added by AI' section). Do not paste JSON or raw data rows."
                ),
            },
            indent=2,
        )

    if tool_name == "search_tickets" and isinstance(parsed, dict) and parsed.get("a2ui_messages"):
        return json.dumps(
            {
                "ok": True,
                "count": parsed.get("count", 0),
                "query": parsed.get("query"),
                "reply_hint": (
                    "Confirm tickets were found and a table was added to the dashboard. "
                    "Do not paste ticket JSON."
                ),
            },
            indent=2,
        )

    if tool_name == "update_dashboard_ui" and isinstance(parsed, dict):
        return json.dumps(
            {
                "ok": parsed.get("ok", True),
                "applied": parsed.get("applied", 0),
                "reply_hint": "Confirm the dashboard change in one short sentence.",
            },
            indent=2,
        )

    return json.dumps(parsed, indent=2, default=str)


def _assistant_message_for_history(
    assistant_message: Any,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    # Gemini requires thought_signature in multi-turn tool calls.
    if provider == "gemini" and hasattr(assistant_message, "model_dump"):
        payload = assistant_message.model_dump(exclude_none=True)
        payload["role"] = "assistant"
        return payload

    # Groq / OpenAI / Ollama — standard format only (no Gemini extra_content).
    entry: dict[str, Any] = {
        "role": "assistant",
        "content": assistant_message.content or "",
    }
    if assistant_message.tool_calls:
        entry["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments or "{}",
                },
            }
            for tool_call in assistant_message.tool_calls
        ]
    return entry


def _format_dashboard_update_reply(actions: list[dict[str, Any]]) -> str:
    remove_count = sum(1 for action in actions if action.get("action") == "remove_surface")
    if remove_count > 1:
        return f"Removed **{remove_count}** agent-added charts from the dashboard."

    parts: list[str] = []
    for action in actions:
        act = action.get("action")
        if act == "remove_surface":
            parts.append("Removed the agent-added chart from the dashboard.")
            continue
        widget_id = str(action.get("widget_id") or "")
        title = get_widget_title(widget_id) or widget_id.replace("_", " ").title()
        props = action.get("props") or {}
        if act == "hide":
            parts.append(f"Hidden **{title}**.")
        elif act == "show":
            parts.append(f"Shown **{title}**.")
        elif act == "configure":
            if "showDataLabels" in props:
                if props.get("showDataLabels"):
                    parts.append(f"Added data labels to **{title}**.")
                else:
                    parts.append(f"Removed data labels from **{title}**.")
            elif props.get("chartType"):
                parts.append(f"Changed **{title}** to a {props['chartType']} chart.")
            else:
                parts.append(f"Updated **{title}**.")
    return " ".join(parts) if parts else "Dashboard updated."


async def _try_fast_add_analytics(
    prompt: str,
    user_id: str,
    filters: AnalyticsFilters,
    explorer_config: dict[str, str],
    history: list[dict[str, str]],
    on_tool_call: Optional[OnToolCall],
    on_a2ui: Optional[OnA2UI],
) -> Optional[str]:
    args = infer_add_analytics_from_prompt(prompt, history)
    if not args:
        return None

    if on_tool_call:
        await on_tool_call("add_analytics_surface", args)

    result = execute_tool(
        "add_analytics_surface",
        args,
        user_id,
        filters,
        explorer_config,
    )
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None

    if on_a2ui:
        a2ui_messages = parsed.get("a2ui_messages")
        surface_id = parsed.get("surface_id")
        if a2ui_messages and surface_id:
            from app.agent.a2ui_builder import _title_from_analytics

            analytics = parsed.get("analytics") or {}
            summary = _title_from_analytics(analytics) if analytics else "Analytics chart"
            await on_a2ui(surface_id, a2ui_messages, str(summary))

    analytics = parsed.get("analytics") or {}
    from app.agent.a2ui_builder import _title_from_analytics

    title = _title_from_analytics(analytics) if analytics else "chart"
    return (
        f"Added **{title}** to the dashboard — scroll to the "
        "**Added by AI** section to view it."
    )


async def _try_fast_dashboard_update(
    prompt: str,
    user_id: str,
    filters: AnalyticsFilters,
    explorer_config: dict[str, str],
    dashboard_context: Optional[dict[str, Any]],
    history: list[dict[str, str]],
    on_tool_call: Optional[OnToolCall],
    on_dashboard_ui: Optional[OnDashboardUI],
) -> Optional[str]:
    bulk_remove = infer_bulk_remove_actions(prompt, dashboard_context)
    if bulk_remove is not None:
        if not bulk_remove:
            return "There are no agent-added charts to remove."
        actions = bulk_remove
    else:
        actions = infer_widget_actions_from_prompt(prompt, history, dashboard_context)
        if not actions and infer_remove_a2ui_from_prompt(prompt):
            surface_ids = (dashboard_context or {}).get("surface_ids") or []
            if surface_ids:
                actions = [{"action": "remove_surface", "surface_id": surface_ids[-1]}]

    if not actions:
        return None

    if on_tool_call:
        await on_tool_call("update_dashboard_ui", {"actions": actions})

    result = execute_tool(
        "update_dashboard_ui",
        {"actions": actions},
        user_id,
        filters,
        explorer_config,
    )
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None

    applied = parsed.get("actions") or []
    if not applied:
        return None

    if on_dashboard_ui:
        await on_dashboard_ui(applied)

    return _format_dashboard_update_reply(applied)


async def run_agent(
    prompt: str,
    history: list[dict[str, str]],
    user_id: str,
    filters: AnalyticsFilters,
    active_tab: Optional[str] = None,
    explorer_config: Optional[dict[str, str]] = None,
    dashboard_context: Optional[dict[str, Any]] = None,
    on_tool_call: Optional[OnToolCall] = None,
    on_dashboard_ui: Optional[OnDashboardUI] = None,
    on_a2ui: Optional[OnA2UI] = None,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    explorer_config = explorer_config or {
        "breakdown_by": "vertical",
        "period": "month",
        "view": "breakdown",
    }

    llm = AgentLLM(settings)
    if not llm.configured:
        yield (
            "The AI assistant is not configured. Set at least one provider in `.env`: "
            "`GROQ_API_KEY`, free `GEMINI_API_KEY`, `OPENAI_API_KEY`, or Ollama locally."
        )
        return

    llm_err = {
        "has_openai_key": llm.has_paid_fallback,
        "has_gemini_key": llm.has_gemini_fallback,
    }

    decline = try_graceful_decline(prompt, history, dashboard_context)
    if decline:
        yield decline
        return

    fast_add = await _try_fast_add_analytics(
        prompt,
        user_id,
        filters,
        explorer_config,
        history,
        on_tool_call,
        on_a2ui,
    )
    if fast_add:
        yield fast_add
        return

    fast_reply = await _try_fast_dashboard_update(
        prompt,
        user_id,
        filters,
        explorer_config,
        dashboard_context,
        history,
        on_tool_call,
        on_dashboard_ui,
    )
    if fast_reply:
        yield fast_reply
        return

    unfulfilled = try_dashboard_unfulfilled_message(prompt, history, dashboard_context)
    if unfulfilled:
        yield unfulfilled
        return

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_context_message(
                filters, active_tab, explorer_config, dashboard_context
            ),
        },
        *history,
        {"role": "user", "content": prompt},
    ]

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = await llm.create_completion(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=1024,
            )
        except asyncio.TimeoutError:
            yield TIMEOUT_MESSAGE
            return
        except AllProvidersExhaustedError as exc:
            yield friendly_llm_error(exc, **llm_err)
            return
        except Exception as exc:
            if "rate limit" in str(exc).lower() or "429" in str(exc):
                yield friendly_llm_error(exc, **llm_err)
                return
            raise

        choice = response.choices[0]
        assistant_message = choice.message

        if assistant_message.tool_calls:
            messages.append(
                _assistant_message_for_history(assistant_message, llm.last_provider_used)
            )

            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name

                try:
                    args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if on_tool_call:
                    await on_tool_call(tool_name, args)

                if tool_name == "update_dashboard_ui":
                    surface_ids = (dashboard_context or {}).get("surface_ids") or []
                    actions = list(args.get("actions") or [])

                    if not actions:
                        inferred_widgets = infer_widget_actions_from_prompt(
                            prompt, history, dashboard_context
                        )
                        if inferred_widgets:
                            actions.extend(inferred_widgets)

                    if infer_remove_a2ui_from_prompt(prompt) and surface_ids:
                        if not any(item.get("action") == "remove_surface" for item in actions):
                            actions.append(
                                {"action": "remove_surface", "surface_id": surface_ids[-1]}
                            )

                    for index, action in enumerate(actions):
                        if action.get("action") != "remove_surface":
                            continue
                        if not action.get("surface_id") and surface_ids:
                            actions[index] = {
                                **action,
                                "surface_id": surface_ids[-1],
                            }

                    args = {**args, "actions": actions}

                if tool_name == "add_analytics_surface":
                    args = merge_add_analytics_args(prompt, args, history)
                    if not args.get("visualization") or args.get("visualization") == "auto":
                        inferred_viz = infer_visualization_from_prompt(prompt)
                        if inferred_viz:
                            args = {**args, "visualization": inferred_viz}

                if tool_name in {"add_analytics_surface", "query_analytics"}:
                    has_months = bool(args.get("include_months")) or bool(
                        (args.get("filter_overrides") or {}).get("months")
                    )
                    if not has_months:
                        inferred = infer_months_from_prompt(prompt)
                        if inferred:
                            args = {**args, "include_months": inferred}

                    overrides = dict(args.get("filter_overrides") or {})
                    if not overrides.get("status"):
                        status = infer_status_from_prompt(prompt) or infer_closure_status_from_prompt(
                            prompt
                        )
                        if status:
                            overrides["status"] = status
                            args = {**args, "filter_overrides": overrides}

                result = execute_tool(
                    tool_name,
                    args,
                    user_id,
                    filters,
                    explorer_config,
                )

                try:
                    parsed_result = json.loads(result)
                except json.JSONDecodeError:
                    parsed_result = None

                if parsed_result and tool_name == "update_dashboard_ui" and on_dashboard_ui:
                    actions = parsed_result.get("actions") or []
                    if actions:
                        await on_dashboard_ui(actions)

                if parsed_result and on_a2ui:
                    a2ui_messages = parsed_result.get("a2ui_messages")
                    surface_id = parsed_result.get("surface_id")
                    if a2ui_messages and surface_id:
                        from app.agent.a2ui_builder import _title_from_analytics

                        analytics = parsed_result.get("analytics") or {}
                        summary = _title_from_analytics(analytics) if analytics else tool_name
                        await on_a2ui(surface_id, a2ui_messages, str(summary))

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": _tool_message_content(result, tool_name),
                    }
                )
            continue

        if assistant_message.content:
            yield assistant_message.content
            return

        break

    try:
        stream = await llm.create_completion(
            messages=messages,
            temperature=0.2,
            max_tokens=1024,
            stream=True,
        )
    except asyncio.TimeoutError:
        yield TIMEOUT_MESSAGE
        return
    except AllProvidersExhaustedError as exc:
        yield friendly_llm_error(exc, **llm_err)
        return
    except Exception as exc:
        if "rate limit" in str(exc).lower() or "429" in str(exc):
            yield friendly_llm_error(exc, **llm_err)
            return
        raise

    yielded = False
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yielded = True
            yield delta

    if not yielded:
        yield FALLBACK_MESSAGE

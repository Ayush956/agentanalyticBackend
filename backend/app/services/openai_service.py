import json
from collections.abc import AsyncGenerator
from typing import Any, Optional

from app.agent.llm_client import AgentLLM, AllProvidersExhaustedError, friendly_llm_error
from app.config import get_settings

SYSTEM_PROMPT = """You are Agent Analytics Intelligence, an AI assistant for Ayush Analytics ticket analytics dashboards.

Rules:
- Answer ONLY using the analytics_context JSON provided by the user.
- Cite specific numbers, labels, and percentages from the data.
- Mention active filters when they affect the answer.
- If the data does not contain enough information, say so clearly — never invent numbers.
- Keep answers concise, structured, and easy to read.
- Use plain English suitable for business stakeholders.
- Format responses in Markdown: use **bold** for key metrics, bullet lists for insights, and Markdown tables when comparing categories (e.g. status counts, vertical breakdowns).
- Example table format:
  | Status | Count | % of Total |
  |--------|------:|-----------:|
  | Closed | 70 | 59.3% |"""


def build_user_message(prompt: str, analytics_context: Optional[dict[str, Any]]) -> str:
    if analytics_context:
        context_json = json.dumps(analytics_context, indent=2, default=str)
        return (
            f"User question: {prompt}\n\n"
            f"Analytics context (JSON):\n{context_json}"
        )
    return (
        f"User question: {prompt}\n\n"
        "No analytics context was provided. Ask the user to apply filters and try again."
    )


async def stream_chat(
    prompt: str,
    analytics_context: Optional[dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    llm = AgentLLM(get_settings())

    if not llm.configured:
        yield (
            "The AI assistant is not configured. "
            "Set GROQ_API_KEY and/or OPENAI_API_KEY in the backend .env file."
        )
        return

    try:
        stream = await llm.create_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(prompt, analytics_context)},
            ],
            temperature=0.2,
            max_tokens=1024,
            stream=True,
        )
    except AllProvidersExhaustedError as exc:
        yield friendly_llm_error(
            exc,
            has_openai_key=llm.has_paid_fallback,
            has_gemini_key=llm.has_gemini_fallback,
        )
        return
    except Exception as exc:
        yield friendly_llm_error(
            exc,
            has_openai_key=llm.has_paid_fallback,
            has_gemini_key=llm.has_gemini_fallback,
        )
        return

    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta

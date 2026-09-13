from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from groq import AsyncGroq
from openai import AsyncOpenAI

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 25

# After Groq 429, skip Groq until this timestamp (auto-failover to next provider).
_groq_backoff_until: float = 0.0


class AllProvidersExhaustedError(Exception):
    def __init__(
        self,
        provider_errors: list[str],
        last_error: Optional[BaseException] = None,
    ) -> None:
        self.provider_errors = provider_errors
        self.last_error = last_error
        super().__init__("All LLM providers failed")


@dataclass
class _Provider:
    name: str
    model: str
    client: Any


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "insufficient_quota" in text
        or "credit_balance" in text
        or "no credits remaining" in text
        or "exceeded your current quota" in text
    )


def _is_gemini_tool_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "thought_signature" in text or (
        "function call" in text and "400" in text and "gemini" in text
    )


def _is_retryable_provider_error(exc: BaseException, provider_name: str) -> bool:
    if _is_rate_limit_error(exc) or _is_quota_error(exc):
        return True
    # Gemini-only: do not treat generic 400s as retryable for Groq/OpenAI.
    if provider_name == "gemini" and _is_gemini_tool_error(exc):
        return True
    return False


def _is_rate_limit_error(exc: BaseException) -> bool:
    if _is_quota_error(exc):
        return False
    name = type(exc).__name__
    if "RateLimit" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "rate limit" in text or "rate_limit" in text or "resource_exhausted" in text


def _parse_retry_after_seconds(exc: BaseException) -> float:
    text = str(exc)
    match = re.search(r"try again in (\d+)m([\d.]+)s", text, re.I)
    if match:
        return int(match.group(1)) * 60 + float(match.group(2))
    match = re.search(r"try again in ([\d.]+)s", text, re.I)
    if match:
        return float(match.group(1))
    # Groq often omits retry-after on daily caps; don't block Groq for a full hour.
    return 300.0


def _clear_groq_backoff() -> None:
    global _groq_backoff_until
    _groq_backoff_until = 0.0


def _mark_groq_rate_limited(exc: BaseException) -> None:
    global _groq_backoff_until
    _groq_backoff_until = time.time() + _parse_retry_after_seconds(exc)
    logger.warning(
        "Groq rate limit hit — failing over to next provider until %s",
        time.strftime("%H:%M:%S", time.localtime(_groq_backoff_until)),
    )


def _groq_is_backoff_active() -> bool:
    return time.time() < _groq_backoff_until


def _insert_after(order: list[str], anchor: str, name: str) -> None:
    if name in order:
        return
    if anchor in order:
        order.insert(order.index(anchor) + 1, name)
    else:
        order.append(name)


def _provider_order(settings: Settings) -> list[str]:
    order = [part.strip().lower() for part in settings.llm_providers.split(",") if part.strip()]
    if not order:
        order = ["groq", "gemini", "openai", "ollama"]

    # Auto-failover chain when keys are configured.
    if settings.groq_api_key and settings.gemini_api_key:
        _insert_after(order, "groq", "gemini")
    if settings.groq_api_key and settings.openai_api_key:
        _insert_after(order, "groq", "openai")

    return order


def _build_providers(settings: Settings) -> list[_Provider]:
    providers: list[_Provider] = []
    seen: set[str] = set()

    for name in _provider_order(settings):
        if name in seen:
            continue

        if name == "groq" and settings.groq_api_key:
            providers.append(
                _Provider(
                    name="groq",
                    model=settings.groq_model,
                    client=AsyncGroq(api_key=settings.groq_api_key),
                )
            )
            seen.add("groq")
        elif name == "gemini" and settings.gemini_api_key:
            providers.append(
                _Provider(
                    name="gemini",
                    model=settings.gemini_model,
                    client=AsyncOpenAI(
                        api_key=settings.gemini_api_key,
                        base_url=settings.gemini_base_url.rstrip("/"),
                    ),
                )
            )
            seen.add("gemini")
        elif name == "openai" and settings.openai_api_key:
            providers.append(
                _Provider(
                    name="openai",
                    model=settings.openai_model,
                    client=AsyncOpenAI(api_key=settings.openai_api_key),
                )
            )
            seen.add("openai")
        elif name == "ollama":
            providers.append(
                _Provider(
                    name="ollama",
                    model=settings.ollama_model,
                    client=AsyncOpenAI(
                        base_url=settings.ollama_base_url.rstrip("/"),
                        api_key="ollama",
                    ),
                )
            )
            seen.add("ollama")

    return providers


class AgentLLM:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.providers = _build_providers(self.settings)
        self.last_provider_used: Optional[str] = None
        # Stick to one provider for multi-step tool loops in a single request.
        self._sticky_provider: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    @property
    def has_paid_fallback(self) -> bool:
        return bool(self.settings.openai_api_key)

    @property
    def has_gemini_fallback(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def _providers_for_attempt(self) -> list[_Provider]:
        if not self._sticky_provider:
            return self.providers
        sticky = [p for p in self.providers if p.name == self._sticky_provider]
        others = [p for p in self.providers if p.name != self._sticky_provider]
        return sticky + others

    async def create_completion(self, **kwargs: Any) -> Any:
        provider_errors: list[str] = []
        last_error: Optional[BaseException] = None

        for provider in self._providers_for_attempt():
            if provider.name == "groq" and _groq_is_backoff_active():
                provider_errors.append("groq (cached rate limit)")
                logger.info(
                    "Skipping Groq — cached rate-limit backoff until %s",
                    time.strftime("%H:%M:%S", time.localtime(_groq_backoff_until)),
                )
                continue

            try:
                result = await asyncio.wait_for(
                    provider.client.chat.completions.create(
                        model=provider.model,
                        **kwargs,
                    ),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                if provider.name == "groq":
                    _clear_groq_backoff()
                self.last_provider_used = provider.name
                self._sticky_provider = provider.name
                if provider_errors:
                    logger.info(
                        "Using %s after previous provider failures: %s",
                        provider.name,
                        ", ".join(provider_errors),
                    )
                return result
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                last_error = exc
                if _is_quota_error(exc):
                    provider_errors.append(f"{provider.name} (no billing credits)")
                    logger.warning("LLM provider %s rejected: insufficient quota", provider.name)
                    continue
                if _is_retryable_provider_error(exc, provider.name):
                    label = provider.name
                    if provider.name == "gemini" and _is_gemini_tool_error(exc):
                        label = f"{provider.name} (tool-calling error)"
                    provider_errors.append(label)
                    logger.warning("LLM provider %s failed, trying next: %s", provider.name, exc)
                    if provider.name == "groq" and _is_rate_limit_error(exc):
                        _mark_groq_rate_limited(exc)
                    # Unstick so failover can pick another provider for this request.
                    if self._sticky_provider == provider.name:
                        self._sticky_provider = None
                    continue
                raise

        raise AllProvidersExhaustedError(provider_errors, last_error)


def friendly_llm_error(
    exc: BaseException,
    *,
    has_openai_key: bool = False,
    has_gemini_key: bool = False,
) -> str:
    if isinstance(exc, AllProvidersExhaustedError):
        last = exc.last_error
        if last and _is_quota_error(last):
            if has_gemini_key:
                return (
                    "Groq limit reached and OpenAI has **no credits**. "
                    "Gemini fallback also failed — check **GEMINI_API_KEY** in `.env`. "
                    "Get a free key at [Google AI Studio](https://aistudio.google.com/app/apikey)."
                )
            return (
                "Groq daily limit is reached and OpenAI has **no credits**. "
                "Add a free **GEMINI_API_KEY** from [Google AI Studio](https://aistudio.google.com/app/apikey) "
                "to `.env` with `LLM_PROVIDERS=groq,gemini` — or wait for Groq to reset."
            )
        if has_gemini_key:
            return (
                "All configured AI providers failed (Groq rate-limited, Gemini/OpenAI unavailable). "
                "Wait for Groq's daily reset or verify your API keys."
            )
        if not has_openai_key:
            return (
                "Groq daily limit is exhausted. Add a free **GEMINI_API_KEY** from "
                "[Google AI Studio](https://aistudio.google.com/app/apikey) to `.env`: "
                "`LLM_PROVIDERS=groq,gemini`"
            )
        return (
            "All AI providers are unavailable. Add **GEMINI_API_KEY** (free) or OpenAI credits, "
            "or wait for Groq's daily reset."
        )

    if _is_quota_error(exc):
        return (
            "This provider has **no billing credits** (OpenAI). "
            "Use free **Gemini** instead: get a key at "
            "[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) "
            "and set `GEMINI_API_KEY` + `LLM_PROVIDERS=groq,gemini` in `.env`."
        )

    if _is_rate_limit_error(exc):
        wait_hint = ""
        match = re.search(r"try again in ([\d]+m[\d.]*s)", str(exc), re.I)
        if match:
            wait_hint = f" Groq resets in about **{match.group(1)}**."
        if has_gemini_key:
            return f"Rate limit hit.{wait_hint} Gemini fallback should take over on retry."
        return (
            f"Groq free-tier limit reached.{wait_hint} "
            "Add free **GEMINI_API_KEY** from [Google AI Studio](https://aistudio.google.com/app/apikey)."
        )

    text = str(exc).strip()
    if len(text) > 280:
        text = text[:277] + "..."
    return text or "Something went wrong while calling the AI provider."

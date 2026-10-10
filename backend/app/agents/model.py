"""Strands model for all agents: the Akamai workshop inference service through Strands'
OpenAI-compatible provider (chat completions only: no Responses API, audio or images).

GuardedOpenAIModel adds the process-wide request limiter, our own 429 handling (wait for
x-ratelimit-reset, at most 2 retries) and last-call status for /api/health. Agents are built
with Strands' own retry strategy disabled so retries never multiply.
"""
import asyncio
import time

from strands.models.openai import OpenAIModel
from strands.types.exceptions import ModelThrottledException

from ..config import redact, settings
from .ratelimit import limiter, mark_call

MAX_429_RETRIES = 2
DEFAULT_RESET_SECONDS = 5.0
MAX_OUTPUT_TOKENS = 1024


class RateLimitExhausted(RuntimeError):
    """Still rate limited after our retries. Deliberately not a ModelThrottledException."""


def llm_configured() -> bool:
    return settings.inference_problem() is None


def reset_seconds(exc: BaseException) -> float:
    """Seconds to wait from the x-ratelimit-reset header of a 429 (fallback 5s)."""
    cause = exc.__cause__ or exc
    response = getattr(cause, "response", None)
    raw = response.headers.get("x-ratelimit-reset") if response is not None else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RESET_SECONDS
    if value > 1e9:  # an epoch timestamp rather than a duration
        value -= time.time()
    return min(max(value, 0.5), 60.0)


class GuardedOpenAIModel(OpenAIModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        for attempt in range(MAX_429_RETRIES + 1):
            await limiter.acquire_async()
            started = False
            try:
                async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                    started = True
                    yield event
                mark_call(True)
                return
            except ModelThrottledException as e:
                mark_call(False, "429 rate limited")
                if started or attempt == MAX_429_RETRIES:
                    raise RateLimitExhausted(f"rate limited (429) after {attempt + 1} attempt(s)") from e
                await asyncio.sleep(reset_seconds(e))
            except Exception as e:
                mark_call(False, f"{type(e).__name__}: {redact(str(e))}")
                raise


def build_model() -> GuardedOpenAIModel:
    settings.require_inference()
    return GuardedOpenAIModel(
        client_args={
            "api_key": settings.openai_api_key,
            "base_url": settings.openai_base_url,
            "timeout": 60,
            "max_retries": 0,  # 429s are handled above, other errors fall back
        },
        model_id=settings.chat_model,
        params={"max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.1},
    )

"""Smoke test for the Akamai workshop inference service. Run from backend/:

    .venv/bin/python scripts/smoke_llm.py

(a) one plain chat completion, (b) a Strands agent that must call a tool, (c) GET /v1/models.
Uses 4-5 requests of the team's 60/min quota. Never prints the API key.
Exit code 0 when everything passes.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openai  # noqa: E402
from strands import Agent, tool  # noqa: E402

from app.agents.model import RateLimitExhausted, build_model  # noqa: E402
from app.agents.ratelimit import limiter  # noqa: E402
from app.config import InferenceConfigError, redact, settings  # noqa: E402


def explain(e: BaseException) -> str:
    """Human-readable failure reason. Never includes the key."""
    seen = e
    while seen is not None:  # Strands wraps the OpenAI error; look down the chain
        if isinstance(seen, openai.AuthenticationError):
            return "401 Unauthorized: bad or revoked OPENAI_API_KEY"
        if isinstance(seen, openai.PermissionDeniedError):
            return "403 Forbidden: key not allowed to use this model/endpoint"
        if isinstance(seen, openai.NotFoundError):
            return "404 Not Found: unsupported endpoint or unknown model alias (check OPENAI_BASE_URL ends in /v1 and CHAT_MODEL)"
        if isinstance(seen, (openai.RateLimitError, RateLimitExhausted)):
            return "429 Too Many Requests: team rate limit hit; wait a minute and retry"
        if isinstance(seen, openai.APITimeoutError):
            return "timeout: the service did not answer in time"
        if isinstance(seen, openai.APIConnectionError):
            return "connection failed: check OPENAI_BASE_URL and your network"
        if isinstance(seen, openai.APIStatusError):
            return f"HTTP {seen.status_code}: {redact(str(seen))[:200]}"
        seen = seen.__cause__
    return redact(f"{type(e).__name__}: {e}")[:300]


def step(title: str):
    print(f"\n== {title}")


def main() -> int:
    try:
        settings.require_inference()
    except InferenceConfigError as e:
        print(f"FAIL: {e}")
        return 1
    print(f"Endpoint: {settings.openai_base_url}\nModel:    {settings.chat_model}\nKey:      configured (not shown)")
    client = openai.OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                           timeout=60, max_retries=0)
    failures: list[str] = []

    step("(a) plain chat completion")
    try:
        limiter.acquire()
        t0 = time.time()
        r = client.chat.completions.create(
            model=settings.chat_model, max_tokens=64, temperature=0,
            messages=[{"role": "user", "content": "Reply with exactly: Closure Call online"}])
        choice = r.choices[0]
        print(f"answer: {choice.message.content!r}\nfinish_reason: {choice.finish_reason}  ({time.time() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        failures.append(f"(a) {explain(e)}")
        print("FAILED:", explain(e))

    step("(b) Strands agent tool call")
    calls: list[tuple[int, int]] = []

    @tool
    def add_numbers(a: int, b: int) -> int:
        """Add two integers and return the sum.

        Args:
            a: First integer.
            b: Second integer.
        """
        calls.append((a, b))
        return a + b

    try:
        t0 = time.time()
        agent = Agent(model=build_model(), tools=[add_numbers], callback_handler=None, retry_strategy=None,
                      system_prompt="Always use the add_numbers tool for arithmetic. Answer with the number.")
        reply = str(agent("What is 1234 + 5678?")).strip()
        print(f"reply: {reply[:200]!r}  ({time.time() - t0:.1f}s)")
        print(f"tool called: {'yes ' + str(calls) if calls else 'NO'}")
        if not calls:
            failures.append("(b) the model answered without calling the tool")
        elif "6912" not in reply.replace(",", ""):
            failures.append("(b) tool was called but the final answer is missing 6912")
    except Exception as e:  # noqa: BLE001
        failures.append(f"(b) {explain(e)}")
        print("FAILED:", explain(e))

    step("(c) GET /v1/models")
    try:
        limiter.acquire()
        ids = [m.id for m in client.models.list().data]
        print("aliases:", ", ".join(ids) or "(none)")
        for wanted in (settings.chat_model, settings.embedding_model):
            if wanted and wanted not in ids:
                print(f"note: {wanted!r} not in the list")
    except Exception as e:  # noqa: BLE001
        failures.append(f"(c) {explain(e)}")
        print("FAILED:", explain(e))

    print()
    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

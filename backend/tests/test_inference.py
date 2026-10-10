"""Inference plumbing: config/secret handling, rate limiting, 429 retries, per-evaluation budget.
No network: the OpenAI provider's stream is replaced with fakes."""
import asyncio
import dataclasses

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from strands.models.openai import OpenAIModel
from strands.types.exceptions import ModelThrottledException

from app import config
from app.agents import model as model_mod
from app.agents import runtime
from app.agents.ratelimit import RequestLimiter, last_call
from app.db import SessionLocal
from app.models import RecordEvent
from tests.test_agents import ScriptedModel, use_model

FAKE_KEY = "hk_live_9f8e7d6c5b4a3210"


# ---------------------------------------------------------------- config & secrets

@pytest.mark.parametrize("key,ok", [("", False), ("hk_your-team-key-here", False), ("<your key>", False),
                                    ("xxxx", False), ("changeme", False), (FAKE_KEY, True)])
def test_inference_problem_detects_missing_and_placeholder(key, ok):
    s = dataclasses.replace(config.settings, openai_api_key=key, openai_base_url="https://x/v1")
    problem = s.inference_problem()
    assert (problem is None) == ok
    if problem:
        assert key not in problem or not key  # never echoes the value
        with pytest.raises(config.InferenceConfigError):
            s.require_inference()


def test_settings_repr_hides_key():
    s = dataclasses.replace(config.settings, openai_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(s)


def test_redact_removes_key_shapes(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, openai_api_key=FAKE_KEY))
    out = config.redact(f"Incorrect API key provided: {FAKE_KEY}; also hk_abcdef123")
    assert FAKE_KEY not in out and "hk_abcdef123" not in out


def test_dotenv_file_beats_shell(tmp_path, monkeypatch):
    """A shell OPENAI_API_KEY from another provider must not win over the .env file."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other-provider")
    monkeypatch.setattr(config, "_file_values", {"API_KEY": FAKE_KEY})
    assert config._pick("OPENAI_API_KEY", "API_KEY") == FAKE_KEY


def test_health_reports_status_without_key(monkeypatch):
    from app.main import app
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, openai_api_key=FAKE_KEY,
                                                                openai_base_url="https://x/v1"))
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["inference"] == "configured" and body["model"] == config.settings.chat_model
    assert "last_model_call" in body and FAKE_KEY not in str(body)
    with TestClient(app) as client:
        monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, openai_api_key=""))
        assert client.get("/api/health").json()["inference"] == "missing"


# ---------------------------------------------------------------- limiter

def test_limiter_caps_requests_per_window():
    lim = RequestLimiter(per_minute=3, window=60)
    assert [lim._reserve() for _ in range(3)] == [0, 0, 0]
    assert lim._reserve() > 59  # 4th must wait for the window


# ---------------------------------------------------------------- 429 handling

def _throttle(reset: str | None):
    """What Strands raises on a 429: ModelThrottledException caused by openai.RateLimitError."""
    response = httpx.Response(429, headers={"x-ratelimit-reset": reset} if reset else {},
                              request=httpx.Request("POST", "https://ws.example/v1/chat/completions"))
    err = ModelThrottledException("rate limited")
    err.__cause__ = openai.RateLimitError("rate limited", response=response, body=None)
    return err


def _guarded(monkeypatch, failures: list):
    """GuardedOpenAIModel whose underlying provider raises the given errors, then succeeds."""
    calls = {"n": 0}

    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        yield {"messageStart": {"role": "assistant"}}

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    monkeypatch.setattr(model_mod.asyncio, "sleep", fake_sleep)
    m = model_mod.GuardedOpenAIModel(client_args={"api_key": "x", "base_url": "http://localhost:1"}, model_id="m")
    return m, calls, sleeps


async def _drain(m):
    return [e async for e in m.stream([])]


def test_429_waits_for_reset_header_then_succeeds(monkeypatch):
    m, calls, sleeps = _guarded(monkeypatch, [_throttle("7"), _throttle(None)])
    assert asyncio.run(_drain(m))
    assert calls["n"] == 3 and sleeps == [7.0, 5.0]  # header value, then the 5s fallback
    assert last_call()["ok"] is True


def test_429_gives_up_after_two_retries(monkeypatch):
    m, calls, _ = _guarded(monkeypatch, [_throttle("1"), _throttle("1"), _throttle("1"), _throttle("1")])
    with pytest.raises(model_mod.RateLimitExhausted):
        asyncio.run(_drain(m))
    assert calls["n"] == 3  # 1 try + 2 retries
    assert last_call()["ok"] is False


# ---------------------------------------------------------------- per-evaluation budget

def test_budget_stops_agent_after_12_model_calls(monkeypatch):
    """A model that keeps calling tools forever is cut off at 12 calls and recorded."""
    from strands import tool

    @tool
    def noop() -> str:
        """Do nothing."""
        return "ok"

    model = ScriptedModel([[("noop", {})]] * 50)
    use_model(monkeypatch, model)
    agent = runtime.make_agent("weather_agent", "test", [noop])
    state = runtime.new_state(evaluation_id="budget-eval-1", facility_id="f")
    with pytest.raises(runtime.AgentUnavailable, match="budget"):
        runtime.invoke(agent, "go", state, timeout=20)
    assert len(model.turns) == 50 - 12  # exactly 12 model calls were allowed
    with SessionLocal() as s:
        ev = s.scalars(select(RecordEvent).where(RecordEvent.evaluation_id == "budget-eval-1",
                                                 RecordEvent.event_type == "llm_budget_reached")).all()
    assert len(ev) == 1 and ev[0].payload["limit"] == 12

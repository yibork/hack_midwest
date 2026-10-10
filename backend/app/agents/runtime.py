"""Running agents with a hard timeout and a clear failure signal for the deterministic fallback."""
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from strands import Agent

from .. import record
from ..config import redact, settings
from .hooks import default_hooks
from .model import build_model, llm_configured


class AgentUnavailable(Exception):
    """The LLM isn't configured, errored, or timed out. Callers fall back to deterministic code."""


def make_agent(name: str, system_prompt: str, tools: list | None = None) -> Agent:
    if not llm_configured():
        raise AgentUnavailable(f"inference not configured: {settings.inference_problem()}")
    return Agent(
        name=name,
        model=build_model(),
        system_prompt=system_prompt,
        tools=tools or [],
        hooks=default_hooks(),
        callback_handler=None,  # no streaming to stdout
        retry_strategy=None,  # 429s are handled once, in GuardedOpenAIModel
    )


def new_state(**ids: Any) -> dict[str, Any]:
    """invocation_state for one agent run. `closed` is set when the caller stops waiting, so
    tools still running in the background refuse to write anything afterwards."""
    return {**ids, "closed": threading.Event()}


def invoke(agent: Agent, prompt: str, state: dict[str, Any], timeout: float,
           structured_output_model=None):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(agent, prompt, invocation_state=state,
                         structured_output_model=structured_output_model)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise AgentUnavailable(f"timed out after {timeout:g}s") from None
    except Exception as e:  # noqa: BLE001 - any model/provider failure means "unavailable"
        raise AgentUnavailable(redact(f"{type(e).__name__}: {e}")) from e
    finally:
        state["closed"].set()
        pool.shutdown(wait=False, cancel_futures=True)


def record_unavailable(agent_name: str, reason: str, fallback: str, state: dict[str, Any]) -> None:
    record.append_now("agent_unavailable", {
        "agent": agent_name, "reason": str(reason), "fallback": fallback,
        "evaluation_id": state.get("evaluation_id"), "decision_id": state.get("decision_id"),
    }, facility_id=state.get("facility_id"), evaluation_id=state.get("evaluation_id"))

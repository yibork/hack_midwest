"""Strands hooks. AuditHook is the decision record for agents; DispatchGuard enforces
"nothing is sent without a Decision" at the tool-call boundary.

Both read evaluation_id / facility_id / decision_id from invocation_state, which callers pass as
agent(prompt, invocation_state={...}).
"""
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

import threading

from .. import record
from ..config import settings
from ..db import SessionLocal
from ..models import Decision

BLOCKED_MESSAGE = "Blocked: no manager approval on record"


class LLMBudgetReached(RuntimeError):
    """Too many model calls for one evaluation; the caller falls back to deterministic code."""


def _short(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ids(state: dict | None) -> dict:
    state = state or {}
    return {k: state.get(k) for k in ("evaluation_id", "facility_id", "decision_id")}


def _message_summary(message: dict | None) -> str:
    parts = []
    for block in (message or {}).get("content", []):
        if "text" in block:
            parts.append(_short(block["text"], 160))
        elif "toolUse" in block:
            parts.append(f"→ {block['toolUse'].get('name')}")
    return " ".join(parts) or "(empty)"


def _tool_result_summary(result: dict | None) -> str:
    if not result:
        return "(no result)"
    texts = [c.get("text") or str(c.get("json", "")) for c in result.get("content", [])]
    return f"{result.get('status', '?')}: {_short(' '.join(texts), 200)}"


class AuditHook(HookProvider):
    """Writes one RecordEvent per agent invocation, model call and tool call."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.before_invocation)
        registry.add_callback(AfterInvocationEvent, self.after_invocation)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def _write(self, event_type: str, event, **payload) -> None:
        ids = _ids(event.invocation_state)
        record.append_now(event_type, {"agent": event.agent.name, **ids, **payload},
                          facility_id=ids["facility_id"], evaluation_id=ids["evaluation_id"])

    def before_invocation(self, event: BeforeInvocationEvent) -> None:
        self._write("agent.invocation_start", event)

    def after_invocation(self, event: AfterInvocationEvent) -> None:
        r = event.result
        summary = f"stop_reason={r.stop_reason}" if r is not None else "done"
        if r is not None and getattr(r, "structured_output", None) is not None:
            summary += " (structured output)"
        self._write("agent.invocation_end", event, summary=summary)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        self._write("agent.model_call_start", event)

    def after_model(self, event: AfterModelCallEvent) -> None:
        if event.exception is not None:
            summary = f"error: {_short(repr(event.exception), 200)}"
        elif event.stop_response is not None:
            summary = f"{event.stop_response.stop_reason}: {_message_summary(event.stop_response.message)}"
        else:
            summary = "(no response)"
        self._write("agent.model_call_end", event, summary=summary)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        self._write("agent.tool_call_start", event, tool=event.tool_use.get("name"),
                    input=event.tool_use.get("input"))

    def after_tool(self, event: AfterToolCallEvent) -> None:
        summary = (f"cancelled: {event.cancel_message}" if event.cancel_message
                   else _tool_result_summary(event.result))
        self._write("agent.tool_call_end", event, tool=event.tool_use.get("name"), summary=summary)


def decision_exists(decision_id: str | None) -> bool:
    if not decision_id or not isinstance(decision_id, str):
        return False
    with SessionLocal() as s:
        return s.get(Decision, decision_id) is not None


class DispatchGuard(HookProvider):
    """Cancels any send_* tool call unless invocation_state carries a stored Decision's id."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.check)

    def check(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name") or ""
        if not name.startswith("send_"):
            return
        state = event.invocation_state or {}
        if decision_exists(state.get("decision_id")):
            return
        event.cancel_tool = BLOCKED_MESSAGE
        ids = _ids(state)
        record.append_now("dispatch_blocked", {
            "agent": getattr(event.agent, "name", None), **ids, "tool": name,
            "input": event.tool_use.get("input"), "reason": BLOCKED_MESSAGE,
        }, facility_id=ids["facility_id"], evaluation_id=ids["evaluation_id"])


class LLMBudget(HookProvider):
    """Caps model calls per budget key (the evaluation_id; notify runs get their own key).
    Over the cap, the model call is refused before any request is sent."""

    _counts: dict[str, int] = {}
    _lock = threading.Lock()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.check)

    @classmethod
    def used(cls, key: str) -> int:
        with cls._lock:
            return cls._counts.get(key, 0)

    def check(self, event: BeforeModelCallEvent) -> None:
        state = event.invocation_state or {}
        if state.get("investigation_budget_key"):
            if state.get("closed") and state["closed"].is_set():
                raise LLMBudgetReached("Investigation closed; refusing late model calls")
            from ..investigations import reserve_model_call
            if not reserve_model_call(state["investigation_budget_key"]):
                raise LLMBudgetReached("Investigation model budget exhausted; deterministic checks remain available")
            return
        key = state.get("llm_budget_key") or state.get("evaluation_id")
        if not key:
            return
        limit = settings.llm_calls_per_evaluation
        with self._lock:
            if len(self._counts) > 5000:  # don't grow forever in a long-running process
                self._counts.clear()
            self._counts[key] = self._counts.get(key, 0) + 1
            count = self._counts[key]
        if count <= limit:
            return
        if count == limit + 1:  # record once
            ids = _ids(state)
            record.append_now("llm_budget_reached", {
                "agent": event.agent.name, **ids, "budget_key": key, "limit": limit,
            }, facility_id=ids["facility_id"], evaluation_id=ids["evaluation_id"])
        raise LLMBudgetReached(f"LLM budget reached ({limit} model calls for this evaluation)")


def default_hooks() -> list[HookProvider]:
    # LLMBudget first so a refused call is never logged as started.
    return [LLMBudget(), AuditHook(), DispatchGuard()]

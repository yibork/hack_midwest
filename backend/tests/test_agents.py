"""Agent safety tests: DispatchGuard, writer validation, deterministic fallback. No network."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from strands.hooks import BeforeToolCallEvent
from strands.models import Model

from app import pipeline
from app.agents import message_agent, runtime, weather_agent, writer_agent
from app.agents.hooks import BLOCKED_MESSAGE, DispatchGuard
from app.db import SessionLocal, utcnow
from app.models import Decision, Facility, Notice, Notification, Recommendation, RecordEvent
from app.schemas import DecisionIn, Evidence
from app.seed import FACILITY_ID, seed
from app.services import record_decision
from app.sources import nws


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def events(event_type: str) -> list[RecordEvent]:
    with SessionLocal() as s:
        return list(s.scalars(select(RecordEvent).where(RecordEvent.event_type == event_type)))


# ---------------------------------------------------------------- fakes

def _ev(kind, value, units=None):
    now = utcnow()
    return Evidence(id=uuid.uuid4().hex, source="nws", kind=kind, value=value, units=units, status="ok",
                    retrieved_at=now, valid_until=now + timedelta(hours=1), relevance_note="canned")


@pytest.fixture
def canned_nws(monkeypatch):
    """Calm weather, no network."""
    def alerts(lat, lon):
        return [], _ev("active_alerts", {"count": 0, "alerts": []})

    def forecast(lat, lon, now=None):
        start = (now or utcnow()).replace(minute=0, second=0, microsecond=0)
        hours = [{"time": (start + timedelta(hours=h)).isoformat(), "wind_mph": 8.0, "gust_mph": 14.0}
                 for h in range(24)]
        return [_ev("point", {"timeZone": "America/Chicago"}),
                _ev("hourly_forecast", {"periods": []}),
                _ev("wind_forecast", {"hours": hours, "peak_gust_mph": 14.0}, "mph")], {"office": "EAX"}

    monkeypatch.setattr(nws, "get_active_alerts_for_point", alerts)
    monkeypatch.setattr(nws, "collect_forecast_evidence", forecast)
    monkeypatch.setattr(nws, "get_point", lambda lat, lon: ({"timeZone": "America/Chicago"}, None))
    monkeypatch.setattr(nws, "get_active_alerts_for_zone",
                        lambda zone: ([], _ev("zone_alerts", {"zone": zone, "count": 0, "alerts": []})))


class FailingModel(Model):
    """A model whose endpoint is unreachable."""

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise ConnectionError("LLM endpoint unreachable")
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        raise ConnectionError("LLM endpoint unreachable")
        yield  # pragma: no cover


class ScriptedModel(Model):
    """Replays canned turns: each turn is a list of (tool_name, input) calls, or a final text."""

    def __init__(self, turns):
        self.turns = list(turns)

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        turn = self.turns.pop(0) if self.turns else "done"
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(turn, str):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": turn}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        for name, args in turn:
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": uuid.uuid4().hex, "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def use_model(monkeypatch, model):
    monkeypatch.setattr(runtime, "llm_configured", lambda: True)
    monkeypatch.setattr(runtime, "build_model", lambda: model)


def make_decision() -> tuple[Recommendation, Decision]:
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))
        d = record_decision(db, rec, DecisionIn(manager_name="Test Manager", action="override",
                                                final_posture="closure", reason="test"))
        return rec, d


# ---------------------------------------------------------------- DispatchGuard

def _tool_event(name, state):
    return BeforeToolCallEvent(agent=SimpleNamespace(name="notify_agent"), selected_tool=None,
                               tool_use={"toolUseId": "t1", "name": name, "input": {"employee_id": "emp-001"}},
                               invocation_state=state)


@pytest.mark.parametrize("state", [{}, {"decision_id": None}, {"decision_id": "does-not-exist"},
                                   {"decision_id": 123}])
def test_dispatch_guard_cancels_send_without_decision(state):
    before = len(events("dispatch_blocked"))
    ev = _tool_event("send_push", {**state, "evaluation_id": "e1", "facility_id": FACILITY_ID})
    DispatchGuard().check(ev)
    assert ev.cancel_tool == BLOCKED_MESSAGE
    blocked = events("dispatch_blocked")
    assert len(blocked) == before + 1
    assert blocked[-1].payload["tool"] == "send_push"


def test_dispatch_guard_allows_send_with_stored_decision(canned_nws):
    _, d = make_decision()
    ev = _tool_event("send_email", {"decision_id": d.id})
    DispatchGuard().check(ev)
    assert ev.cancel_tool is False


def test_dispatch_guard_ignores_non_send_tools():
    ev = _tool_event("fetch_alerts_for_facility", {})
    DispatchGuard().check(ev)
    assert ev.cancel_tool is False


def test_agent_send_without_decision_is_blocked_end_to_end(monkeypatch):
    """A real Strands agent loop: the model asks to send, the guard cancels, nothing is sent.
    (No agent has send tools today; the guard stays as a second lock for any that are added.)"""
    from strands import tool

    sent = []

    @tool
    def send_push(employee_id: str, message: str) -> str:
        """Send a push message."""
        sent.append(employee_id)
        return "sent"

    model = ScriptedModel([[("send_push", {"employee_id": "emp-001", "message": "Avery: closed"})], "done"])
    use_model(monkeypatch, model)
    agent = runtime.make_agent("guard_test_agent", "test", [send_push])
    state = runtime.new_state(evaluation_id="guard-e2e", facility_id=FACILITY_ID, decision_id="nope")
    before = len(events("dispatch_blocked"))
    runtime.invoke(agent, "send", state, timeout=10)
    assert len(events("dispatch_blocked")) == before + 1 and sent == []
    with SessionLocal() as s:
        ends = s.scalars(select(RecordEvent).where(RecordEvent.evaluation_id == "guard-e2e",
                                                   RecordEvent.event_type == "agent.tool_call_end")).all()
    assert ends and "cancelled" in ends[0].payload["summary"]


# ---------------------------------------------------------------- writer validation

FACTS = {"triggers": [{"threshold": 58, "observed": 61.0, "units": "mph"}],
         "evidence": [{"retrieved": "11:13 CDT"}], "operating_date": "Monday, October 12, 2026"}


def test_writer_rejects_wrong_posture():
    text = "NWS data retrieved 11:13 CDT shows gusts of 61.0 mph, so the office should move to telework."
    reason = writer_agent.validate_rationale(text, "closure", FACTS)
    assert reason and "telework" in reason


def test_writer_rejects_number_not_in_facts():
    text = "Closure is recommended: NWS forecast gusts of 65 mph (retrieved 11:13 CDT)."
    reason = writer_agent.validate_rationale(text, "closure", FACTS)
    assert reason and "65" in reason


def test_writer_accepts_bare_posture_label():
    text = ("The rules engine recommends a normal operating posture for Monday, October 12 "
            "based on NWS data retrieved 11:13 CDT.")
    assert writer_agent.validate_rationale(text, "normal", FACTS) is None
    # ...but naming a different posture is still rejected
    assert writer_agent.validate_rationale(text + " Telework is optional.", "normal", FACTS)


def test_writer_rejects_missing_posture_and_too_long():
    assert "does not state" in writer_agent.validate_rationale("Gusts of 61.0 mph.", "closure", FACTS)
    assert "too long" in writer_agent.validate_rationale("closure " * 141, "closure", FACTS)
    assert writer_agent.validate_rationale("closure " * 130, "closure", FACTS) is None  # 140 is the hard limit


def test_writer_accepts_faithful_paragraph():
    text = ("For Monday, October 12, the rules engine recommends closure: NWS forecast gusts reach "
            "61.0 mph in the commute window, above the 58 mph threshold (retrieved 11:13 CDT). "
            "A manager must approve before staff are notified.")
    assert writer_agent.validate_rationale(text, "closure", FACTS) is None


def test_rejected_draft_falls_back_to_template(monkeypatch, canned_nws):
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))  # calm -> normal
    from app.schemas import Recommendation as RecSchema
    rec_s = RecSchema.model_validate(rec)
    monkeypatch.setattr(writer_agent, "draft", lambda *a, **k: ("The office should switch to telework today.", "normal"))
    before = len(events("writer_rejected"))
    text, source, label = writer_agent.write_rationale(
        rec_s, [], facility_name="X", tz="America/Chicago",
        state=runtime.new_state(evaluation_id=rec.evaluation_id, facility_id=FACILITY_ID), timeout=5)
    assert source == "template" and text == rec_s.rationale
    assert label.startswith("AI draft rejected: names posture") and label.endswith("template used")
    rejected = events("writer_rejected")
    assert len(rejected) == before + 1 and "telework" in rejected[-1].payload["reason"]


# ---------------------------------------------------------------- pipeline fallback

def test_pipeline_falls_back_when_model_unreachable(monkeypatch, canned_nws):
    use_model(monkeypatch, FailingModel())
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))
        assert rec.rationale_note == "AI draft pending"  # shown at once; the writer runs in the background
        writer_agent.wait(rec.id)
        db.refresh(rec)
        assert rec.rationale_source == "template" and rec.rationale_note == "LLM unavailable - template used"
        assert rec.posture == "normal"
        assert rec.rationale.startswith("For USDA Kansas City Field Office")
        kinds = {e.kind for e in weather_agent.evidence_for(rec.evaluation_id)}
        assert {"active_alerts", "wind_forecast"} <= kinds  # tools were called directly
        unavailable = db.scalars(select(RecordEvent).where(RecordEvent.evaluation_id == rec.evaluation_id,
                                                           RecordEvent.event_type == "agent_unavailable")).all()
        assert {e.payload["agent"] for e in unavailable} == {"weather_agent", "writer_agent"}
        assert all("unreachable" in e.payload["reason"] for e in unavailable)


def test_pipeline_without_llm_config_uses_template(canned_nws):
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))
    assert rec.rationale_source == "template"


def _notice_rows(decision_id):
    with SessionLocal() as s:
        notice = s.scalar(select(Notice).where(Notice.decision_id == decision_id))
        return notice, s.scalars(select(Notification).where(Notification.notice_id == notice.id)).all()


def test_messages_fall_back_to_templates_when_model_unreachable(monkeypatch, canned_nws):
    use_model(monkeypatch, FailingModel())
    _, d = make_decision()  # override -> the message agent is allowed to draft, and fails
    _, rows = _notice_rows(d.id)
    personal = [n for n in rows if n.included and n.message_type in ("action_required", "fyi")]
    assert personal and all(n.message and n.drafted_by == "template" for n in personal)
    assert any(e.payload["agent"] == "message_agent" for e in events("agent_unavailable"))


def test_message_agent_drafts_all_groups_in_one_call(monkeypatch, canned_nws):
    """One model call returns templates per group. Good drafts are used for every person in the
    group; a draft without {instruction}, or one inventing a time, falls back to the template."""
    from app.notify.notices import plan_for

    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))
        plan = plan_for(db, db.get(Facility, FACILITY_ID), rec, "closure")
    groups = {}
    for p in plan.planned:
        if p.included and p.facts and p.facts.lang == "en":
            groups.setdefault(p.facts.instruction, p.group_key)
    good_key = groups["office closed, telework"]
    bad_key = groups["office closed, follow agency leave policy"]
    timed_key = groups["report as normal and acknowledge"]
    drafts = {"groups": [
        {"key": good_key, "subject": "{facility} closed {date}",
         "body": "{what_happened}\n**{personal_action}**\n{access_line}\n{reply_line}\n{footer}"},
        {"key": bad_key, "subject": "Closed", "body": "Hi {name}, the office is closed. Building: {access}."},
        {"key": timed_key, "subject": "Report", "body": "{what_happened}\n**{personal_action}**\n{access_line}\n{reply_line}\n{footer}\nReport by 06:45."},
    ]}
    model = ScriptedModel([[("NoticeDrafts", drafts)], "done"])
    use_model(monkeypatch, model)
    calls_before = len([e for e in events("agent.model_call_start") if e.payload.get("agent") == "message_agent"])
    stats = message_agent.fill_messages(plan, allow_llm=True, evaluation_id=rec.evaluation_id,
                                        facility_id=FACILITY_ID, budget_key="test-drafts")
    calls = [e for e in events("agent.model_call_start") if e.payload.get("agent") == "message_agent"]
    assert stats["drafted"] == 2  # bad_key dropped: no {instruction} placeholder
    by_key = {}
    for p in plan.planned:
        if p.included and p.facts:
            by_key.setdefault(p.group_key, set()).add(p.drafted_by)
    assert by_key[good_key] == {"agent"}
    assert by_key[bad_key] == {"template"}
    assert by_key[timed_key] == {"template"}  # 06:45 isn't a computed time
    agent_msg = next(p for p in plan.planned if p.group_key == good_key)
    assert "Don't come in; work from home" in agent_msg.message
    assert any("06:45" in r["reason"] for r in stats["rejected"])
    assert len(calls) - calls_before == 1  # ONE model call for the whole notice



def test_writer_allows_conditional_and_negated_postures():
    facts = {"what_would_change": ["Gusts reaching 58 mph during the commute window would move this to closure."],
             "operating_date": "Tuesday, October 13, 2026"}
    ok = ("The rules engine recommends normal operations for Tuesday, October 13. No closure is warranted. "
          "Gusts of 58 mph would move this to closure. A manager must approve before staff are notified.")
    assert writer_agent.validate_rationale(ok, "normal", facts, "normal") is None
    assert writer_agent.validate_rationale("No closure is warranted; normal operations.", "normal", facts) is None


def test_writer_rejects_stated_posture_mismatch_and_numbers():
    facts = {"what_would_change": ["Gusts reaching 58 mph would move this to closure."]}
    text = "The rules engine recommends normal operations."
    assert "stated_posture" in writer_agent.validate_rationale(text, "normal", facts, "closure")
    assert "61" in writer_agent.validate_rationale(text + " Gusts of 61 mph would move this to closure.", "normal", facts)
    assert writer_agent.validate_rationale("The office should close today; closure recommended.", "normal", facts)



def test_writer_retries_once_after_a_length_rejection(monkeypatch):
    from app.schemas import Recommendation as RecSchema
    rec = RecSchema(id="r", facility_id="f", evaluated_at=utcnow(), operating_date=utcnow().date(), posture="normal",
                    triggers=[], confidence="high", confidence_reasons=["ok"], what_would_change=[],
                    rationale="Template text.", evidence_ids=[], policy_version="t")
    calls = []

    def fake_draft(rec, facts, state, timeout, feedback=None):
        calls.append(feedback)
        return ("word " * 150 + "normal operations.", "normal") if not feedback else (
            "The rules engine recommends normal operations; a manager must approve.", "normal")
    monkeypatch.setattr(writer_agent, "draft", fake_draft)
    state = runtime.new_state(evaluation_id=f"retry-{uuid.uuid4().hex[:6]}", facility_id="f")
    text, source, label = writer_agent.write_rationale(rec, [], facility_name="X", tz="America/Chicago",
                                                       state=state, timeout=5)
    assert source == "agent" and label == "AI draft" and len(calls) == 2
    # the retry states the exact count and how much to cut
    assert calls[1].startswith("it has 152 words; the hard limit is 140. Cut at least 42 words")

    monkeypatch.setattr(writer_agent, "draft", lambda *a, **k: ("word " * 150 + "normal operations.", "normal"))
    text, source, label = writer_agent.write_rationale(rec, [], facility_name="X", tz="America/Chicago",
                                                       state=state, timeout=5)
    assert source == "template" and label.startswith("AI draft rejected: too long") and text == "Template text."


def test_writer_label_when_llm_unavailable(monkeypatch):
    from app.schemas import Recommendation as RecSchema
    rec = RecSchema(id="r", facility_id="f", evaluated_at=utcnow(), operating_date=utcnow().date(), posture="normal",
                    triggers=[], confidence="high", confidence_reasons=[], what_would_change=[],
                    rationale="Template text.", evidence_ids=[], policy_version="t")
    use_model(monkeypatch, FailingModel())
    _, source, label = writer_agent.write_rationale(rec, [], facility_name="X", tz="America/Chicago",
                                                    state=runtime.new_state(evaluation_id="u1", facility_id="f"), timeout=5)
    assert source == "template" and label == "LLM unavailable - template used"



def test_writer_allows_threshold_references():
    """Live draft from 2026-10-10: naming the posture a threshold belongs to is not recommending it."""
    facts = {"what_would_change": ["Gusts reaching 46 mph would move this to delayed_arrival; 58 mph to closure."],
             "x": [13, 11.5, 2026, 11, 12, 18, 24, 59]}
    draft = ("The rules engine recommends normal operations for the USDA Kansas City Field Office on Tuesday, October 13, "
             "2026. NWS data retrieved at 18:24 CDT shows no active alerts. The forecast indicates clear skies with peak "
             "commute gusts of 11.5 mph, well below the 46 mph threshold for delayed arrival. Gusts of 58 mph would move "
             "this to closure. A manager must approve before staff are notified.")
    assert writer_agent.validate_rationale(draft, "normal", facts, "normal") is None
    assert writer_agent.validate_rationale(draft + " Staff should plan a delayed arrival.", "normal", facts)



def test_recommendation_is_returned_before_the_writer_and_then_updated(monkeypatch, canned_nws):
    """The page shows the template at once ("AI draft pending"); a valid AI draft replaces it later."""
    import threading
    release = threading.Event()

    def slow_draft(rec, facts, state, timeout, feedback=None):
        release.wait(10)
        return (f"The rules engine recommends normal operations for {facts['operating_date']}. "
                "A manager must approve before staff are notified.", "normal")
    monkeypatch.setattr(runtime, "llm_configured", lambda: True)
    monkeypatch.setattr(writer_agent, "draft", slow_draft)
    monkeypatch.setattr(weather_agent, "run", lambda *a, **k: (_ for _ in ()).throw(runtime.AgentUnavailable("off")))
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(Facility, FACILITY_ID))
        assert rec.rationale_note == "AI draft pending" and rec.rationale == rec.template_rationale
        template = rec.template_rationale
        release.set()
        writer_agent.wait(rec.id)
        db.refresh(rec)
    assert rec.rationale_source == "agent" and rec.rationale_note == "AI draft"
    assert rec.rationale.startswith("The rules engine recommends normal operations") and rec.template_rationale == template
    updated = [e for e in events("rationale.updated") if e.payload["recommendation_id"] == rec.id]
    assert len(updated) == 1 and updated[0].payload["template"] == template and updated[0].payload["ai_draft"] == rec.rationale

"""Operating calendar (federal holidays, next operating day, explicit/demo targets, weekend crews,
critical-function calendars), evidence reuse by snapshot hash, skipped drafting, additive
migration. No network."""
import dataclasses
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, inspect, select

from app import calendar, models, pipeline, schemas
from app.agents import message_agent
from app.db import SessionLocal, add_missing_columns
from app.notify.impact import Exposure
from app.notify.plan import build_plan, context_for
from app.seed import ACCESS_SEGMENTS, FACILITY_ID, roster_shifts, seed
from app.services import store_evidence_now
from tests.test_agents import ScriptedModel, use_model

CT = pipeline.ZoneInfo("America/Chicago")


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def fac(**kw):
    base = dict(id=FACILITY_ID, name="KC Office", lat=39.0997, lon=-94.5786, open_time="07:30", close_time="16:00",
                access_routes=ACCESS_SEGMENTS)
    return schemas.Facility(**{**base, **kw})


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=CT).astimezone(timezone.utc)


# ---------------------------------------------------------------- federal holidays

def test_federal_holidays_2026_include_columbus_day_and_observed_days():
    h = calendar.federal_holidays(2026)
    assert h[date(2026, 10, 12)] == "Columbus Day"
    assert h[date(2026, 7, 3)] == "Independence Day"  # July 4 is a Saturday -> observed Friday
    assert h[date(2026, 11, 26)] == "Thanksgiving Day" and h[date(2026, 1, 19)] == "Martin Luther King Jr. Day"
    assert len(h) == 11
    assert calendar.holiday(date(2027, 12, 31)) == "New Year's Day"  # Jan 1, 2028 is a Saturday


@pytest.mark.parametrize("start,expected,why", [
    (date(2026, 10, 10), date(2026, 10, 13),
     "Sat Oct 10-Sun Oct 11 is the weekend; Mon Oct 12 is a federal holiday (Columbus Day); next workday Tue Oct 13."),
    (date(2026, 10, 12), date(2026, 10, 13), "Mon Oct 12 is a federal holiday (Columbus Day); next workday Tue Oct 13."),
    (date(2026, 10, 14), date(2026, 10, 14), "Next operating day: Wed Oct 14."),
])
def test_next_operating_day(start, expected, why):
    assert calendar.next_operating_day(start) == (expected, why)


# ---------------------------------------------------------------- evaluation target

def test_default_target_is_next_operating_day_and_rolls_after_close():
    day, mode, why = pipeline.choose_target(fac(), at(2026, 10, 9, 10))  # Friday before close
    assert (day, mode) == (date(2026, 10, 9), "next_operating_day")
    day, _, why = pipeline.choose_target(fac(), at(2026, 10, 9, 17))  # Friday after close
    assert day == date(2026, 10, 13) and "Columbus Day" in why and "next workday Tue Oct 13" in why


def test_explicit_weekend_target_says_who_is_affected():
    day, mode, why = pipeline.choose_target(fac(), at(2026, 10, 9, 10), target_date=date(2026, 10, 10))
    assert (day, mode) == (date(2026, 10, 10), "explicit")
    assert why.startswith("Sat Oct 10 is not an operating day") and "weekend crew" in why


def test_demo_workday_needs_demo_mode(monkeypatch):
    with pytest.raises(ValueError, match="DEMO_MODE"):
        pipeline.choose_target(fac(), at(2026, 10, 10, 9), demo_workday=True)
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(pipeline.settings, demo_mode=True))
    day, mode, why = pipeline.choose_target(fac(), at(2026, 10, 10, 9), demo_workday=True)
    assert (day, mode) == (date(2026, 10, 10), "demo_workday") and why == "Saturday is treated as a workday for this demonstration."


def test_recommendation_states_target_and_why():
    from app.rules.engine import evaluate
    from tests.test_rules import alerts_ev, wind_ev

    rec = evaluate(fac(), [alerts_ev(), wind_ev()], at(2026, 10, 10, 9), operating_date=date(2026, 10, 13),
                   target_note="Mon Oct 12 is a federal holiday (Columbus Day); next workday Tue Oct 13.")
    assert rec.operating_date == date(2026, 10, 13) and rec.target_note
    assert "Target day: Mon Oct 12 is a federal holiday" in rec.rationale


# ---------------------------------------------------------------- weekends: only people scheduled that day

def _plan(day, posture="closure", workday_override=False):
    with SessionLocal() as db:
        from app.notify.plan import load_inputs
        facility = db.get(models.Facility, FACILITY_ID)
        people, assignments, shifts, functions, names = load_inputs(db, facility, day)
        if workday_override:
            shifts = [s for s in shifts if s.date != day] + roster_shifts(day, workday=True)
        ctx = context_for(facility, day, posture, at(day.year, day.month, day.day, 3), functions,
                          demo_workday=workday_override)
        return build_plan(ctx, people, assignments, shifts, functions, Exposure(), base_url="http://t",
                          facility_names=names)


def _next(weekday: int) -> date:
    d = datetime.now(CT).date()
    while d.weekday() != weekday or calendar.holiday(d):
        d += timedelta(days=1)
    return d


def test_weekend_closure_only_affects_the_weekend_crew():
    plan = _plan(_next(5))  # next Saturday
    acting = sorted(i.employee.name for i in plan.impacts if i.message_type == "action_required")
    assert acting == sorted(["Ava Brennan", "Jordan Pike", "Priya Natarajan", "Rafael Ortiz", "Tina Walsh",
                             "Selena Brooks"])
    assert sum(1 for i in plan.impacts if "DAY_OFF" in i.codes) > 20


def test_critical_functions_follow_their_own_calendar(monkeypatch):
    from dataclasses import replace
    from app.notify import plan as plan_module
    monkeypatch.setattr(plan_module, "settings", replace(plan_module.settings, staffing_feature=True))
    sat = {c.code: c for c in _plan(_next(5), posture="normal").coverage}
    assert sat["animal_care"].required == 2 and sat["animal_care"].status == "covered"  # 7-day weekend crew
    assert sat["it_ops"].required == 0  # Mon-Fri function
    weekday = {c.code: c for c in _plan(_next(1), posture="normal").coverage}
    assert weekday["it_ops"].required == 1


def test_demo_workday_uses_the_weekday_schedule():
    plan = _plan(_next(5), workday_override=True)
    assert plan.ctx.workday and sum(1 for i in plan.impacts if i.message_type == "action_required") > 25


# ---------------------------------------------------------------- evidence reuse, skipped drafting

def test_same_response_is_not_stored_twice_in_an_evaluation():
    eid = f"dedupe-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    def ev():
        return schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="active_alerts", value={"alerts": []},
                                retrieved_at=now, snapshot_hash="a" * 64, status="ok")
    first, second = ev(), ev()
    store_evidence_now([first], FACILITY_ID, eid)
    store_evidence_now([second], FACILITY_ID, eid)  # e.g. the fallback re-running a tool the agent ran
    with SessionLocal() as s:
        rows = s.scalars(select(models.Evidence).where(models.Evidence.evaluation_id == eid)).all()
    assert len(rows) == 1 and second.id == first.id
    store_evidence_now([ev()], FACILITY_ID, f"{eid}-other")  # another evaluation keeps its own row


def test_no_drafting_call_when_nobody_gets_a_message(monkeypatch):
    model = ScriptedModel([[("NoticeDrafts", {"groups": []})], "done"])
    use_model(monkeypatch, model)
    plan = _plan(_next(1), posture="normal")
    assert not [p for p in plan.planned if p.included and p.facts]
    stats = message_agent.fill_messages(plan, allow_llm=True, evaluation_id=None, facility_id=FACILITY_ID,
                                        budget_key=f"skip-{uuid.uuid4().hex[:6]}")
    assert stats["skipped"] and len(model.turns) == 2  # the model was never called


# ---------------------------------------------------------------- additive migration

def test_new_nullable_columns_are_added_to_an_existing_database(tmp_path):
    """Production's tables predate operating_days / target_mode / target_note."""
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    md = MetaData()
    new = {"operating_days", "target_mode", "target_note"}
    for name in ("facilities", "critical_functions", "recommendations"):
        Table(name, md, *[Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
                          for c in models.Base.metadata.tables[name].columns if c.name not in new])
    md.create_all(eng)
    added = add_missing_columns(eng)
    assert sorted(added) == ["critical_functions.operating_days", "facilities.operating_days",
                             "recommendations.target_mode", "recommendations.target_note"]
    assert "operating_days" in {c["name"] for c in inspect(eng).get_columns("facilities")}
    assert add_missing_columns(eng) == []  # idempotent

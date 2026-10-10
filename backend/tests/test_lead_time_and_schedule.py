"""Hourly coverage for a target day days ahead (real NWS fixture), forecast lead-time confidence
caps, scheduled re-evaluations (deterministic first), lead time in the decision record, road item
categories / active-since, and plain rejection reasons. No network."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import models, pipeline, scheduler, schemas
from app.agents import weather_agent
from app.agents.local import facts as F
from app.db import SessionLocal
from app.rules.engine import compute_windows, evaluate, load_policy
from app.seed import ACCESS_SEGMENTS, FACILITY_ID, seed
from app.services import record_decision
from app.sources import local_signals as ls
from app.sources import nws
from tests.test_agents import canned_nws  # noqa: F401 (fixture)
from tests.test_rules import alerts_ev, wind_ev

CT = ZoneInfo("America/Chicago")
FIX = Path(__file__).parent / "fixtures"
FAC = schemas.Facility(id=FACILITY_ID, name="KC Office", lat=39.0997, lon=-94.5786, open_time="07:30",
                       close_time="16:00", access_routes=ACCESS_SEGMENTS)


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def hourly_ev():
    """The real NWS hourly response (156 periods, Sat Oct 10 18:00 CDT -> Sat Oct 17 06:00 CDT)."""
    data = json.loads((FIX / "nws_hourly_kc.json").read_text())
    periods = [{"start": p["startTime"], "end": p["endTime"], "temperature": p["temperature"],
                "short_forecast": p["shortForecast"]} for p in data["properties"]["periods"]]
    return schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="hourly_forecast", status="ok",
                            value={"periods": periods}, retrieved_at=datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc))


def windows(day: date):
    w = compute_windows(FAC, datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc), load_policy(), day)
    return w, {"commute": (w.commute_start, w.commute_end), "departure": (w.departure_start, w.departure_end)}


# ---------------------------------------------------------------- hourly coverage (real fixture)

def test_target_three_days_ahead_finds_the_right_hours():
    w, wins = windows(date(2026, 10, 13))
    ev = hourly_ev()
    assert nws.hourly_coverage(ev, wins) == [] and ev.status == "ok"
    starts = [p["start"] for p in nws.periods_in(ev.value["periods"], w.commute_start, w.commute_end)]
    # 05:30-07:30 CDT overlaps the 05:00, 06:00 and 07:00 periods of Tue Oct 13 (UTC-5), not Oct 12/14
    assert starts == ["2026-10-13T05:00:00-05:00", "2026-10-13T06:00:00-05:00", "2026-10-13T07:00:00-05:00"]


def test_window_past_the_forecast_is_unknown_not_calm():
    _, wins = windows(date(2026, 10, 17))  # the fixture ends Sat Oct 17 06:00
    ev = hourly_ev()
    missing = nws.hourly_coverage(ev, wins)
    assert ev.status == "unknown" and missing == ["departure window (Sat Oct 17 16:00-18:00 local)"]
    assert ev.value["error"] == "no hourly periods found for departure window (Sat Oct 17 16:00-18:00 local)"
    rec = evaluate(FAC, [alerts_ev(), wind_ev(), ev], datetime(2026, 10, 17, 4, 0, tzinfo=CT).astimezone(timezone.utc),
                   operating_date=date(2026, 10, 17))
    assert rec.confidence == "low"
    assert any("no hourly periods found for departure window" in r for r in rec.confidence_reasons)
    assert "no notable" not in " ".join(rec.confidence_reasons).lower()


def test_weather_tool_reports_missing_hours_and_notes_say_data_missing(monkeypatch):
    def forecast(lat, lon, now=None):
        return [schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="point", status="ok", value={},
                                 retrieved_at=now), hourly_ev(), wind_ev()], {"office": "EAX"}
    monkeypatch.setattr(nws, "collect_forecast_evidence", forecast)
    eid = f"hourly-{uuid.uuid4().hex[:8]}"
    out = weather_agent.fetch_forecast(FAC, eid, datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc),
                                       operating_date=date(2026, 10, 17))
    assert out["hourly_missing"] == ["departure window (Sat Oct 17 16:00-18:00 local)"]
    hourly = next(e for e in weather_agent.evidence_for(eid) if e.kind == "hourly_forecast")
    assert hourly.status == "unknown"
    notes = weather_agent.WeatherNotes(notes=[weather_agent.EvidenceNote(
        evidence_id=hourly.id, relevance_note="Hourly forecast; no notable commute hazards.")])
    weather_agent.apply_notes(notes, FAC.id, eid)
    hourly = next(e for e in weather_agent.evidence_for(eid) if e.kind == "hourly_forecast")
    assert hourly.relevance_note.startswith("Data missing: no hourly periods found") and "no notable" not in hourly.relevance_note


# ---------------------------------------------------------------- lead time caps confidence

def wind_for(day: date):
    ev = wind_ev()
    hours = [{"time": datetime.combine(day, datetime.min.time().replace(hour=h), CT).astimezone(timezone.utc).isoformat(),
              "wind_mph": 10.0, "gust_mph": 15.0} for h in range(0, 24)]
    return ev.model_copy(update={"value": {"hours": hours}})


@pytest.mark.parametrize("hours_before,confidence", [(10, "high"), (30, "medium"), (60, "low")])
def test_lead_time_caps_confidence(hours_before, confidence):
    day = date(2026, 10, 13)
    w, _ = windows(day)
    now = w.commute_start - timedelta(hours=hours_before)
    fresh = {"retrieved_at": now, "valid_until": now + timedelta(hours=1)}
    rec = evaluate(FAC, [alerts_ev().model_copy(update=fresh), wind_for(day).model_copy(update=fresh)], now,
                   operating_date=day)
    assert rec.confidence == confidence and rec.lead_time_hours == hours_before
    capped = [r for r in rec.confidence_reasons if r.startswith("Forecast is")]
    assert bool(capped) == (hours_before > 24)
    if capped:
        assert f"Forecast is {hours_before} h out; NWS watches/warnings for this day may not be issued yet" in capped[0]


# ---------------------------------------------------------------- scheduled re-evaluations

def test_jobs_are_created_for_1500_the_day_before_and_0400_on_the_day():
    day = date.today() + timedelta(days=5)
    with SessionLocal() as db:
        facility = db.get(models.Facility, FACILITY_ID)
        keys = scheduler.schedule_for(db, facility, day)
        assert scheduler.schedule_for(db, facility, day) == []  # idempotent
        jobs = {j.kind: j.run_at.astimezone(CT) for j in db.scalars(select(models.ScheduledJob).where(
            models.ScheduledJob.key.in_(keys)))}
        assert jobs["day_before_1500"] == datetime.combine(day - timedelta(days=1), datetime.min.time().replace(hour=15), CT)
        assert jobs["target_0400"] == datetime.combine(day, datetime.min.time().replace(hour=4), CT)
        assert scheduler.next_run(db, FACILITY_ID, day) == jobs["day_before_1500"]


def test_tick_runs_due_jobs_once_and_skips_past_days(monkeypatch):
    ran = []
    monkeypatch.setattr(pipeline, "recheck", lambda db, f, d, job_key=None: ran.append(job_key) or {"changed": False})
    day = date.today() + timedelta(days=3)
    with SessionLocal() as db:
        scheduler.schedule_for(db, db.get(models.Facility, FACILITY_ID), day)
        old = models.ScheduledJob(key=f"{FACILITY_ID}|2020-01-01|target_0400", facility_id=FACILITY_ID,
                                  operating_date=date(2020, 1, 1), kind="target_0400",
                                  run_at=datetime(2020, 1, 1, 10, tzinfo=timezone.utc), status="pending", result={})
        db.merge(old)
        db.commit()
    due = datetime.combine(day - timedelta(days=1), datetime.min.time().replace(hour=15, minute=1), CT)
    mine = f"{FACILITY_ID}|{day.isoformat()}|day_before_1500"
    scheduler.tick(due)
    assert mine in ran and f"{FACILITY_ID}|{day.isoformat()}|target_0400" not in ran  # not due yet
    ran.clear()
    scheduler.tick(due)
    assert mine not in ran  # done jobs never run twice
    with SessionLocal() as db:
        assert db.get(models.ScheduledJob, mine).status == "done"
        assert db.get(models.ScheduledJob, old.key).status == "skipped"


def _agent_events(eid: str) -> list[str]:
    with SessionLocal() as db:
        return [e.event_type for e in db.scalars(select(models.RecordEvent).where(
            models.RecordEvent.evaluation_id == eid)) if e.event_type.startswith("agent")]


def test_recheck_is_deterministic_and_reevaluates_only_on_change(monkeypatch, canned_nws):
    day, _, _ = pipeline.choose_target(FAC, datetime.now(timezone.utc))
    with SessionLocal() as db:
        facility = db.get(models.Facility, FACILITY_ID)
        had = db.scalar(select(models.Recommendation).where(models.Recommendation.operating_date == day).limit(1))
        first = pipeline.recheck(db, facility, day)  # brings the day's recommendation up to date
    if had is None:
        assert first["changed"] and "no earlier recommendation" in first["changes"][0]
    assert _agent_events(first["evaluation_id"]) == []  # the deterministic check itself never uses agents
    full = []
    monkeypatch.setattr(pipeline, "evaluate_facility", lambda *a, **k: full.append(1))
    with SessionLocal() as db:
        again = pipeline.recheck(db, db.get(models.Facility, FACILITY_ID), day)
    assert again["changed"] is False and full == []  # unchanged: no full run, no new recommendation
    assert _agent_events(again["evaluation_id"]) == []


# ---------------------------------------------------------------- decision record: lead time

def test_decision_records_lead_time():
    with SessionLocal() as db:
        rec = models.Recommendation(
            id=uuid.uuid4().hex, facility_id=FACILITY_ID, evaluated_at=datetime.now(timezone.utc),
            operating_date=date.today() + timedelta(days=2), posture="normal", triggers=[], confidence="low",
            confidence_reasons=[], what_would_change=[], rationale="t", rationale_source="template", evidence_ids=[],
            policy_version="t")
        db.add(rec)
        db.commit()
        d = record_decision(db, rec, schemas.DecisionIn(manager_name="M", action="approve"))
        ev = db.scalars(select(models.RecordEvent).where(models.RecordEvent.event_type == "decision.recorded")).all()
    payload = next(e.payload for e in ev if e.payload["decision_id"] == d.id)
    assert 24 < payload["lead_time_hours"] < 72


# ---------------------------------------------------------------- road items: category, active since

def test_road_items_have_category_and_active_since(monkeypatch):
    src = next(s for s in ls.sources() if s.id == "modot_traveler")
    ls.clear_cache()
    monkeypatch.setattr(ls, "http_get", lambda s, url, method="GET": (FIX / "local" / "modot.json").read_bytes())
    monkeypatch.setattr(ls, "save_snapshot", lambda raw: "x" * 64)
    r = ls.fetch(src, now=datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc))
    by = {i["title"][:20]: i for i in r.items}
    flood = by["IS 35 is CLOSED Nort"]
    assert flood["category"] == "incident" and flood["active_since"].startswith("2026-10-10T10:10")
    wz = next(i for i in r.items if "MO-291" in i["title"])
    assert wz["category"] == "construction" and wz["active_since"].startswith("2025-12-22")  # not "new"
    assert r.to_evidence().relevance_note.endswith(f"{len(r.items)} active items")
    ls.clear_cache()


def test_rejection_reasons_are_plain():
    it = {"item_id": "i", "title": "I-35 closed", "excerpt": "Road closed", "link": "x", "points": []}
    from app.agents.local.run import geo_context
    _, bad = F.validate([F.LocalFact(item_id="i", fact_type="road_closure", location_text="x",
                                     supporting_quote="not in the item", confidence="high")],
                        {"i": it}, "roads", geo_context(FAC, []))
    assert bad == [{"item_id": "i", "title": "I-35 closed", "reason": "quote not found in source"}]

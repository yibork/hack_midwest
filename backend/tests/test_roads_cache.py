"""Road facts cache (reuse when road sources are unchanged), the background watcher's 10-minute
limit, and preliminary recommendations finalized when a live roads check finishes. No network."""
import dataclasses
import json
import random
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models, pipeline, schemas
from app.agents.local import roads_cache, run as local_run, specialists
from app.db import SessionLocal, utcnow
from app.main import app
from app.seed import FACILITY_ID, seed
from app.sources import local_signals as ls
from tests.test_agents import canned_nws  # noqa: F401 (fixture)
from tests.test_local_signals import FACILITY, LIB, OP

FIX = Path(__file__).parent / "fixtures" / "local"
FILES = {"modot_traveler": "modot.json", "kandrive_events": "kandrive.json", "kcscout_incidents": "kcscout.json"}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture
def feeds(monkeypatch):
    """Road sources from fixtures; a test can swap one source's bytes to simulate a change."""
    raw = {sid: (FIX / f).read_bytes() for sid, f in FILES.items()}

    def fetch(src, now=None):
        if src.agent != "roads":
            return ls.FeedResult(src, "ok", [])
        items = ls.PARSERS["json_list"](src, raw[src.id], now or utcnow())
        return ls.FeedResult(src, "ok", items, snapshot_hash=uuid.uuid4().hex)  # raw changes every time
    monkeypatch.setattr(ls, "fetch", fetch)
    roads_cache.reset_rate_limit()
    return raw


@pytest.fixture
def roads_agent(monkeypatch):
    """Count roads-agent model runs; return one validated fact per call. calls.gate pauses the run."""
    class Calls(list):
        gate: threading.Event

    calls = Calls()

    def run_or_fallback(agent, items, fac, commute, state, timeout, use_model, geo):
        calls.append(agent)
        gate.wait(10)
        it = next(i for i in items if "IS 35" in i["title"])
        fact = {"item_id": it["item_id"], "fact_type": "road_condition", "location_text": it["title"],
                "routes_mentioned": ["I-35"], "counties": [], "effective_from": None, "effective_until": None,
                "relevance": "not_relevant", "distance_km": 1.0, "segments": [], "matches_facility_access_route": False,
                "matches_employee_commute_routes": [], "affected_employee_ids": [], "supporting_quote": it["title"],
                "confidence": "high", "downgraded": False}
        return [fact], "agent", []
    calls.gate = gate = threading.Event()
    gate.set()
    monkeypatch.setattr(specialists, "run_or_fallback", run_or_fallback)
    return calls


def evidence_events(eid, kind):
    with SessionLocal() as s:
        return s.scalars(select(models.RecordEvent).where(models.RecordEvent.evaluation_id == eid,
                                                          models.RecordEvent.event_type == kind)).all()


DAY = utcnow().date()


def test_unchanged_sources_reuse_facts_without_a_model_call(feeds, roads_agent):
    first = local_run.run(FACILITY, f"c1-{uuid.uuid4().hex[:6]}", [], [OP, LIB], day=DAY)
    assert roads_agent == ["roads"] and first.roads_key
    eid = f"c2-{uuid.uuid4().hex[:6]}"
    second = local_run.run(FACILITY, eid, [], [OP, LIB], day=DAY)
    assert roads_agent == ["roads"]  # no second model call
    assert second.roads_key == first.roads_key and second.pending == []
    [reused] = evidence_events(eid, "local.roads_reused")
    assert reused.payload["note"].startswith("roads facts reused from") and reused.payload["note"].endswith("(sources unchanged)")
    with SessionLocal() as s:
        facts = s.scalars(select(models.Evidence).where(models.Evidence.evaluation_id == eid,
                                                        models.Evidence.kind == "local_fact")).all()
    assert len(facts) == 1 and facts[0].value["extracted_by"] == "cache"


def test_reordered_feed_with_new_record_ids_is_still_a_hit(feeds, roads_agent):
    day = DAY + timedelta(days=4)  # own target day: the cache is shared across tests
    local_run.run(FACILITY, f"r1-{uuid.uuid4().hex[:6]}", [], [OP], day=day)
    rows = json.loads(feeds["modot_traveler"])
    random.Random(4).shuffle(rows)
    for i, r in enumerate(rows):
        r["OID"] = 1000 + i  # MoDOT reassigns these every time it regenerates the feed
    feeds["modot_traveler"] = json.dumps(rows).encode()
    local_run.run(FACILITY, f"r2-{uuid.uuid4().hex[:6]}", [], [OP], day=day)
    assert roads_agent == ["roads"]


def test_changed_source_content_is_a_miss(feeds, roads_agent):
    day = DAY + timedelta(days=5)
    local_run.run(FACILITY, f"m1-{uuid.uuid4().hex[:6]}", [], [OP], day=day)
    rows = json.loads(feeds["kcscout_incidents"])
    rows["d"]["Incident"][0]["Description"] += "<br />ALL LANES NOW OPEN"
    feeds["kcscout_incidents"] = json.dumps(rows).encode()
    local_run.run(FACILITY, f"m2-{uuid.uuid4().hex[:6]}", [], [OP], day=day)
    assert roads_agent == ["roads", "roads"]


def test_background_watcher_runs_on_change_at_most_once_per_10_minutes(feeds, roads_agent):
    day = DAY + timedelta(days=3)
    assert local_run.refresh_roads(FACILITY, day, [OP])["action"] == "ran"
    assert local_run.refresh_roads(FACILITY, day, [OP])["action"] == "unchanged"
    rows = json.loads(feeds["kandrive_events"])
    rows[0]["eventDescription"]["descriptionHeader"] = "I-435 northbound: Road reopened."
    feeds["kandrive_events"] = json.dumps(rows).encode()
    deferred = local_run.refresh_roads(FACILITY, day, [OP])
    assert deferred["action"] == "deferred" and deferred["changed"] == ["kandrive_events"]
    roads_cache.reset_rate_limit()  # 10 minutes later
    assert local_run.refresh_roads(FACILITY, day, [OP])["action"] == "ran"
    assert roads_agent == ["roads", "roads"]
    # an evaluation for that day now hits the cache the watcher filled
    lp = local_run.run(FACILITY, f"w-{uuid.uuid4().hex[:6]}", [], [OP], day=day)
    assert lp.pending == [] and roads_agent == ["roads", "roads"]


def test_live_roads_check_gives_a_preliminary_recommendation_then_final(monkeypatch, canned_nws, feeds, roads_agent):
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(pipeline.settings, local_signals=True))
    monkeypatch.setattr(local_run.coordinator, "choose", lambda *a, **k: {
        "agents": ["roads"], "model_agents": ["roads"], "by": "rules", "reason": "test", "hazards": [],
        "dropped_for_budget": [], "budget_left": 9})
    roads_agent.gate.clear()  # the live roads check is slow
    with SessionLocal() as db:
        rec = pipeline.evaluate_facility(db, db.get(models.Facility, FACILITY_ID))
        assert rec.status == "preliminary" and rec.rationale_note == "Preliminary - road check in progress"
        rec_id, eid = rec.id, rec.evaluation_id
    client = TestClient(app)
    blocked = client.post(f"/api/recommendations/{rec_id}/decision", json={"manager_name": "M", "action": "approve"})
    assert blocked.status_code == 409 and "road check in progress" in blocked.json()["detail"]
    roads_agent.gate.set()
    pipeline.wait_final(rec_id)
    with SessionLocal() as db:
        rec = db.get(models.Recommendation, rec_id)
        assert rec.status == "final" and rec.rationale_note != "Preliminary - road check in progress"
        assert any(e.kind == "local_fact" for e in db.scalars(select(models.Evidence).where(
            models.Evidence.id.in_(rec.evidence_ids))))
    [fin] = evidence_events(eid, "recommendation.finalized")
    assert set(fin.payload) >= {"preliminary", "final", "changed"}
    # same sources again: cache hit, final at once
    with SessionLocal() as db:
        again = pipeline.evaluate_facility(db, db.get(models.Facility, FACILITY_ID))
    assert again.status == "final" and roads_agent == ["roads"]



def test_people_summary_reconciles_minor_road_conditions():
    """Ramp closures in commute corridors are shown as minor, next to 0 exposed commutes."""
    from app import services
    with SessionLocal() as db:
        facility = db.get(models.Facility, FACILITY_ID)
        day, _, _ = pipeline.choose_target(schemas.Facility.model_validate(facility), utcnow())
        onsite = [e.id for e in db.scalars(select(models.Employee).where(models.Employee.role == "employee")).all()]
        ev = models.Evidence(
            id=uuid.uuid4().hex, facility_id=FACILITY_ID, source="local", kind="local_fact", status="ok",
            retrieved_at=utcnow(), relevance_note="x",
            value={"fact_type": "road_condition", "title": "Ramp MYRTLE AVE TO IS70W is CLOSED Westbound",
                   "supporting_quote": "Ramp MYRTLE AVE TO IS70W is CLOSED", "affected_employee_ids": onsite[:40],
                   "relevance": "commute", "authority": "official_state"})
        db.add(ev)
        rec = models.Recommendation(
            id=uuid.uuid4().hex, facility_id=FACILITY_ID, evaluated_at=utcnow(), operating_date=day, posture="normal",
            triggers=[], confidence="high", confidence_reasons=[], what_would_change=[], rationale="t",
            rationale_source="template", evidence_ids=[ev.id], policy_version="t")
        db.add(rec)
        db.commit()
        people = services.people_summary(db, rec, facility)
    assert people.exposed_count == 0
    assert people.minor_commute_count > 0 and people.minor_commute_label == "ramp closures"

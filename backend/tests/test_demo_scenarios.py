import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import api as api_routes, db as database, demo, investigations, models, record, schemas, seed, services
from app.config import settings
from app.agents import message_agent, weather_agent
from app.main import app
from app.notify import escalation, exposure, notices
from app import demo_location, memo
from app.sources import nws, local_signals
from app.rules.engine import evaluate, load_policy


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/demo.db", connect_args={"check_same_thread": False})
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    for module in (database, seed, record, services, investigations, exposure, escalation, message_agent, weather_agent):
        monkeypatch.setattr(module, "SessionLocal", factory)
    config = replace(settings, demo_mode=True, demo_topic_suffix="test-suite", snapshot_dir=tmp_path / "snapshots")
    for module in (demo, seed, record, investigations, exposure, escalation, notices, api_routes):
        monkeypatch.setattr(module, "settings", config)
    seed.seed()
    with factory() as db:
        yield db
    engine.dispose()


def storm(db):
    rec = demo.synthetic(db, db.get(models.Facility, seed.FACILITY_ID))
    db.commit()
    return rec


def test_personas_have_stable_ids_and_demo_topics(demo_db):
    expected = {"mgr": "Maria Alvarez", "ava": "Dana R.", "jordan": "Jordan Pike", "sam": "Sam Okafor",
                "noor": "Lee Chen", "priya": "Priya Shah", "hank": "Pat Moreno"}
    for key, name in expected.items():
        person = demo_db.get(models.Employee, seed.emp_id(key))
        assert person.name == name
        assert person.ntfy_topic.endswith("-test-suite")
    shifts = {s.employee_id: s for s in seed.roster_shifts(database.utcnow().date(), workday=True)}
    assert shifts[seed.emp_id("ava")].shift_start == "05:00"
    assert shifts[seed.emp_id("jordan")].status == "day_off"
    assert shifts[seed.emp_id("noor")].status == "telework"
    seed.seed()  # repeated deploys don't append the suffix again
    assert demo_db.get(models.Employee, seed.emp_id("ava")).ntfy_topic.endswith("-test-suite")


def test_storm_runs_normal_policy_and_people_engine(demo_db):
    rec = storm(demo_db)
    assert rec.posture == "closure" and rec.confidence == "high"
    assert {t["rule_id"] for t in rec.triggers} >= {"high_wind", "winter_storm", "access_route_closed"}
    assert "I-70" in next(t["observed"] for t in rec.triggers if t["rule_id"] == "access_route_closed")
    plan = notices.plan_for(demo_db, demo_db.get(models.Facility, rec.facility_id), rec, rec.posture)
    animal = next(c for c in plan.coverage if c.code == "animal_care")
    assert animal.required == 2 and animal.available == ["Dana R."]
    assert animal.gap == 1 and [b.name for b in animal.backups] == ["Jordan Pike"]
    assert next(i for i in plan.impacts if i.employee.name == "Priya Shah").exposed
    lee = [p for p in plan.planned if p.employee.name == "Lee Chen" and p.included]
    assert lee and all(p.channels == ["email"] and p.message_type == "fyi" for p in lee)
    dana = next(p for p in plan.planned if p.employee.name == "Dana R." and p.message_type == "action_required")
    assert dana.requires_ack
    for e in demo_db.scalars(select(models.Evidence)):
        assert e.scenario_kind == "simulated" and "SIMULATED" in e.relevance_note


def test_national_demo_filters_and_ranks_live_policy_hazards(demo_db, monkeypatch):
    now = database.utcnow()
    def feature(id, event="High Wind Warning", severity="Severe", urgency="Immediate", certainty="Observed", hours=1, status="Actual"):
        return {"geometry": {"type": "Point", "coordinates": [-105, 40]}, "properties": {
            "id": id, "event": event, "severity": severity, "urgency": urgency, "certainty": certainty,
            "status": status, "onset": (now + timedelta(hours=hours)).isoformat(),
            "expires": (now + timedelta(hours=max(hours + 2, 5))).isoformat(), "areaDesc": "Boulder, CO"}}
    data = {"features": [feature("unsupported", "Red Flag Warning", "Extreme"),
        feature("minor", severity="Minor"), feature("tomorrow", hours=25), feature("test", status="Test"),
        feature("likely", certainty="Likely"), feature("observed"), feature("future", urgency="Expected"),
        feature("moderate", "Wind Advisory", "Moderate"), feature("ongoing", "Flood Warning", hours=-2),
        feature("sixth", "Winter Storm Warning", "Moderate", "Future")]}
    raw = json.dumps(data).encode()
    monkeypatch.setattr(nws, "_fetch", lambda *a, **k: (raw, now))
    alerts, evidence = nws.get_active_alerts_national(limit=5, demo_only=True)
    assert [a.id for a in alerts] == ["observed", "ongoing", "likely", "future", "moderate"]
    assert record.settings.snapshot_dir.joinpath(evidence[0].snapshot_hash + ".json").read_bytes() == raw
    assert all(a.lat == 40 and a.lon == -105 for a in alerts)


def test_national_zone_fallback_is_allowlisted_and_saved(demo_db, monkeypatch):
    now = database.utcnow()
    zone = nws.BASE + "/zones/forecast/COZ039"
    feature = {"geometry": None, "properties": {"id": "zone-warning", "event": "Winter Storm Warning",
        "severity": "Severe", "certainty": "Likely", "urgency": "Expected", "affectedZones": [zone],
        "onset": now.isoformat(), "expires": (now+timedelta(hours=12)).isoformat()}}
    urls = []
    def fetch(url, params=None):
        urls.append(url)
        data = {"features": [feature]} if url.endswith("/alerts/active") else {"geometry": {
            "type": "Polygon", "coordinates": [[[-106, 39], [-104, 39], [-104, 41], [-106, 41], [-106, 39]]]}}
        return json.dumps(data).encode(), now
    monkeypatch.setattr(nws, "_fetch", fetch)
    alerts, evidence = nws.get_active_alerts_national(limit=5, demo_only=True)
    assert alerts[0].lat == 40 and alerts[0].lon == -105
    assert len(evidence) == 2 and all(e.snapshot_hash for e in evidence)
    feature["properties"]["affectedZones"] = ["https://evil.invalid/zone"]
    urls.clear()
    alerts, _ = nws.get_active_alerts_national(limit=5, demo_only=True)
    assert alerts[0].lat is None and all(url.startswith(nws.BASE) for url in urls)


def relocate_fixture(db, monkeypatch):
    now = database.utcnow().replace(hour=16, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(demo_location, "utcnow", lambda: now)
    alert = schemas.NationalAlert(id="real-warning", event="High Wind Warning", severity="Severe", certainty="Observed",
        urgency="Immediate", onset=(now-timedelta(hours=2)).isoformat(), ends=(now+timedelta(hours=12)).isoformat(),
        area_desc="Boulder, CO", lat=40, lon=-105)
    scan = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="national_alerts", status="ok", value={},
        retrieved_at=now-timedelta(minutes=1), valid_until=now+timedelta(minutes=14), url=nws.BASE+"/alerts/active",
        snapshot_hash=record.save_snapshot(json.dumps({"properties": alert.model_dump()}).encode()))
    point = {"timeZone": "America/Denver", "office": "BOU"}
    point_ev = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="point", value=point, status="ok", retrieved_at=now)
    wind = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="wind_forecast", value={"hours": []}, status="ok", retrieved_at=now)
    # A newer empty point result must not hide the explicitly selected real alert.
    empty = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="active_alerts", value={"alerts": []}, status="ok", retrieved_at=now)
    monkeypatch.setattr(nws, "get_point", lambda *a, **k: (point, point_ev))
    monkeypatch.setattr(nws, "collect_point_evidence", lambda *a, **k: ([point_ev, wind, empty], point))
    rec = demo_location.relocate(db, db.get(models.Facility, seed.FACILITY_ID), alert, [scan])
    return rec, scan


def test_live_relocation_preserves_roster_topics_functions_and_records(demo_db, monkeypatch):
    from app.sources import airnow
    air_locations = []
    monkeypatch.setattr(airnow, "collect", lambda facility, day, **kw: air_locations.append((facility.lat, facility.lon, day)) or [])
    original = demo_db.get(models.Facility, seed.FACILITY_ID)
    before = [(e.id, e.name, e.ntfy_topic) for e in demo_db.scalars(select(models.Employee))]
    rec, scan = relocate_fixture(demo_db, monkeypatch)
    facility = demo_db.get(models.Facility, rec.facility_id)
    assert facility.demo_origin_id == seed.FACILITY_ID and not facility.temporary
    assert facility.timezone == "America/Denver" and facility.nws_office == "BOU"
    assert air_locations == [(facility.lat, facility.lon, rec.operating_date)]
    assert original.lat != facility.lat and original.timezone == "America/Chicago"
    assert before == [(e.id, e.name, e.ntfy_topic) for e in demo_db.scalars(select(models.Employee))]
    inputs = notices.load_inputs(demo_db, facility, rec.operating_date)
    original_inputs = notices.load_inputs(demo_db, original, rec.operating_date)
    assert {e.id for e in inputs[0]} == {e.id for e in original_inputs[0]}
    assert [f.code for f in inputs[3]] == [f.code for f in original_inputs[3]]
    assert [r["route"] for r in facility.access_routes] == [r["route"] for r in original.access_routes]
    assert facility.access_routes[0]["points"] != original.access_routes[0]["points"]
    assert rec.posture == "closure" and rec.scenario_kind is None
    selected = next(e for e in services.recommendation_detail(demo_db, rec).evidence if e.value.get("selected_live_alert"))
    assert next(t for t in rec.triggers if t["rule_id"] == "high_wind")["evidence_id"] == selected.id
    assert selected.snapshot_hash == scan.snapshot_hash and selected.retrieved_at == scan.retrieved_at
    decision = services.record_decision(demo_db, rec, schemas.DecisionIn(action="approve", manager_name="Maria Alvarez"))
    notice = demo_db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    assert notice and notices.plan_for_notice(demo_db, notice).planned
    rows = list(demo_db.scalars(select(models.Notification).where(models.Notification.notice_id == notice.id,
                                                                              models.Notification.included.is_(True))))
    assert rows and all("Synthetic facility relocated" in n.message and "DEMO (real NWS)" in n.subject for n in rows)
    assert services.recommendation_detail(demo_db, rec).people.exposure_status == "unknown"
    rendered = memo.render(demo_db, decision)
    assert "Boulder" in rendered and "High Wind" in rendered and selected.snapshot_hash in rendered
    assert record.verify(demo_db, rec.facility_id)["ok"]


def test_relocated_missing_sources_remain_unknown_on_refresh(demo_db, monkeypatch):
    rec, _ = relocate_fixture(demo_db, monkeypatch)
    evidence = services.recommendation_detail(demo_db, rec).evidence
    missing = [e for e in evidence if e.kind in {"home_zone_alerts", "local_feed", "river_categories"}]
    assert missing and all(e.status == "unknown" and "not available for this location" in e.relevance_note for e in missing)
    assert rec.confidence == "low"
    assert any("not available for this location" in r for r in rec.confidence_reasons)
    monkeypatch.setattr(nws, "get_active_alerts_for_zone", lambda *a, **k: pytest.fail("KC home-zone request at relocated site"))
    fresh = exposure.collect(rec.facility_id, uuid.uuid4().hex, [])
    assert fresh.status == "unknown" and "zones" not in fresh.value
    from app.agents.local.run import LocalPass
    monkeypatch.setattr(local_signals, "fetch", lambda *a, **k: pytest.fail("KC local source requested at relocated site"))
    lp = LocalPass(schemas.Facility.model_validate(demo_db.get(models.Facility, rec.facility_id)), uuid.uuid4().hex,
                   rec.operating_date, [], None, use_models=False)
    lp.start([], plan_override={"agents": ["roads"], "model_agents": []}).finish()
    fresh_rows = weather_agent.evidence_for(lp.evaluation_id)
    assert fresh_rows and all(e.status == "unknown" for e in fresh_rows)


def test_relocated_demo_selection_revision_and_reset_keep_history(demo_db, monkeypatch):
    from app import people_api, mapdata
    rec, scan = relocate_fixture(demo_db, monkeypatch)
    current = demo.state(demo_db)
    current.mode, current.active_facility_id, current.recommendation_id = "live_national", rec.facility_id, rec.id
    current.label = rec.scenario_context["label"]
    demo_db.commit()
    assert demo.active_recommendation(demo_db, seed.FACILITY_ID).id == rec.id
    assert people_api.facility_of(demo_db, seed.emp_id("sam")).id == rec.facility_id
    assert api_routes.list_facilities(demo_db)[0].id == rec.facility_id
    assert mapdata.map_for(demo_db, rec)["corridor_cells"] == []
    assert "not available" in mapdata.map_for(demo_db, rec)["corridor_rule"]
    assert rec.id in {r["id"] for r in demo.recordings(demo_db)}
    accepted = investigations.request(demo_db, rec, schemas.InvestigationIn(action="what_would_change"), launch=False)
    inv = demo_db.get(models.Investigation, accepted["investigation_id"])
    evidence = services.recommendation_detail(demo_db, rec).evidence
    revised = investigations.revise(demo_db, inv, rec, evidence)
    assert revised.target_note == rec.target_note and revised.scenario_context["live_relocated"]
    decision = services.record_decision(demo_db, revised, schemas.DecisionIn(action="approve", manager_name="Maria Alvarez"))
    demo.reset(demo_db)
    assert demo_db.get(models.Decision, decision.id).archived_at
    assert demo_db.get(models.Evidence, scan.id)
    assert record.verify(demo_db, rec.facility_id)["ok"]


def test_demo_national_requires_explicit_current_choice(demo_db, monkeypatch):
    with pytest.raises(HTTPException, match="choose"):
        demo.set_scenario(demo.ScenarioIn(mode="live_national"), demo_db)
    monkeypatch.setattr(nws, "get_active_alerts_national", lambda **kwargs: ([], []))
    with pytest.raises(HTTPException, match="no longer"):
        demo.set_scenario(demo.ScenarioIn(mode="live_national", alert_id="expired"), demo_db)


def test_polygon_centroid_is_area_weighted_and_handles_dateline():
    # Extra vertices on one side must not pull the centroid away from the area's centre.
    assert nws._centroid({"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 0.5], [2, 1], [2, 2], [0, 2], [0, 0]]]}) == (1, 1)
    lat, lon = nws._centroid({"type": "Polygon", "coordinates": [[[179, 0], [-179, 0], [-179, 2], [179, 2], [179, 0]]]})
    assert lat == 1 and abs(lon) == 180


@pytest.mark.parametrize("event,rule_id,posture", [("Winter Storm Warning", "winter_storm", "closure"),
    ("Ice Storm Warning", "ice_storm", "closure"), ("Winter Weather Advisory", "winter_advisory", "delayed_arrival")])
def test_verified_winter_categories_drive_normal_rules(demo_db, event, rule_id, posture):
    now = database.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    fac = schemas.Facility.model_validate(demo_db.get(models.Facility, seed.FACILITY_ID))
    ev = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="active_alerts", status="ok", retrieved_at=now,
        value={"alerts": [{"event": event, "onset": now.isoformat(), "ends": (now+timedelta(hours=12)).isoformat()}]})
    rec = evaluate(fac, [ev], now, operating_date=now.astimezone(timezone(timedelta(hours=-5))).date())
    assert rec.posture == posture and rec.triggers[0].rule_id == rule_id
    rule = next(r for r in load_policy().rules if r["id"] == rule_id)
    assert rule["verified"] and "weather.gov" in rule["citation"]["url"]


def test_reset_archives_state_preserves_record_and_saved_copies(demo_db):
    rec = storm(demo_db)
    decision = services.record_decision(demo_db, rec, schemas.DecisionIn(action="approve", manager_name="Maria Alvarez"))
    request = investigations.request(demo_db, rec, schemas.InvestigationIn(action="explain_confidence"), launch=False)
    question = models.StaffQuestion(id=uuid.uuid4().hex, investigation_id=request["investigation_id"],
            employee_id=seed.emp_id("hank"), token_hash=uuid.uuid4().hex, question="Safe?", options=["Safe"],
            expires_at=database.utcnow() + timedelta(hours=2), delivery={})
    demo_db.add(question)
    demo_db.add(models.ScheduledJob(key="demo-pending", facility_id=rec.facility_id, operating_date=rec.operating_date,
                                   kind="manager_recheck", run_at=database.utcnow()+timedelta(hours=1)))
    demo_db.commit()
    tables = (models.Evidence, models.Recommendation, models.Decision, models.Notice, models.Notification,
              models.Investigation, models.StaffQuestion)
    counts = {m: demo_db.scalar(select(func.count()).select_from(m)) for m in tables}
    chain = [(e.id, e.payload) for e in demo_db.scalars(select(models.RecordEvent).order_by(models.RecordEvent.id))]
    snapshots = {p.name: p.read_bytes() for p in record.settings.snapshot_dir.glob("*.json")}
    result = demo.reset(demo_db)
    assert result["recordings_retained"]
    assert all(demo_db.scalar(select(func.count()).select_from(m)) == count for m, count in counts.items())
    assert [(e.id, e.payload) for e in demo_db.scalars(select(models.RecordEvent).order_by(models.RecordEvent.id))][:len(chain)] == chain
    assert demo_db.scalars(select(models.RecordEvent).order_by(models.RecordEvent.id.desc())).first().event_type == "demo_reset"
    assert {p.name: p.read_bytes() for p in record.settings.snapshot_dir.glob("*.json")} == snapshots
    assert demo_db.get(models.Decision, decision.id).archived_at
    assert demo_db.get(models.ScheduledJob, "demo-pending").status == "skipped"
    assert record.verify(demo_db, rec.facility_id)["ok"]
    assert escalation.tick() == []
    with pytest.raises(HTTPException, match="reset"):
        investigations.submit_staff(demo_db, question, "Safe")
    client = TestClient(app)
    assert client.get(f"/api/facilities/{rec.facility_id}/latest-recommendation").json() is None
    assert client.post(f"/api/recommendations/{rec.id}/decision", json={"action": "approve", "manager_name": "Maria"}).status_code == 409


def test_replay_and_simulation_never_leak_into_live_or_refetch(demo_db, monkeypatch):
    rec = storm(demo_db)
    played = demo.replay(demo_db, demo_db.get(models.Facility, rec.facility_id), rec.id)
    demo_db.commit()
    live = exposure.load(demo_db, rec.facility_id, "live-evaluation", evidence_ids=[])
    assert not live.alerts and not live.closed_routes and not live.closed_for
    assert played.scenario_kind == "replay" and "REPLAY" in played.rationale
    saved = demo_db.scalars(select(models.Evidence).where(models.Evidence.id.in_(played.evidence_ids))).all()
    assert all(e.scenario_kind == "replay" and e.retrieved_at == rec.evaluated_at for e in saved)
    monkeypatch.setattr(investigations, "run_check", lambda *a: pytest.fail("Scenario fetched live data"))
    pending = investigations.request(demo_db, played, schemas.InvestigationIn(action="recheck_now"), launch=False)
    investigations.run(pending["investigation_id"])
    demo_db.expire_all()
    row = demo_db.get(models.Investigation, pending["investigation_id"])
    assert row.status == "completed" and row.result["tools_used"] == [], row.result
    assert demo_db.get(models.Recommendation, row.revised_recommendation_id).scenario_kind == "replay"


def test_demo_controls_disabled_and_role_guarded(demo_db, monkeypatch):
    client = TestClient(app)
    assert client.post("/api/demo/reset", headers={"X-Role": "security_desk"}).status_code == 403
    monkeypatch.setattr(demo, "settings", replace(demo.settings, demo_mode=False))
    assert client.post("/api/demo/reset").status_code == 403
    assert client.post("/api/demo/scenario", json={"mode": "simulated_winter"}).status_code == 403

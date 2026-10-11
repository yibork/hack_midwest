import uuid
from dataclasses import replace
from datetime import timedelta

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
    assert {t["rule_id"] for t in rec.triggers} >= {"high_wind", "access_route_closed"}
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

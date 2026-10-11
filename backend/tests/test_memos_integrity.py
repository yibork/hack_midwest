"""No live inference, notifications or external fetches: fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update, insert
from sqlalchemy.orm import sessionmaker

from app import db as database, models, record, schemas, seed, services, snapshots, memo
from app.main import app


def test_schema_upgrade_only_adds_nullable_columns(tmp_path):
    from sqlalchemy import MetaData, Table, Column, Integer, Text, String, JSON, inspect
    legacy = MetaData()
    Table("record_events", legacy, Column("id", Integer, primary_key=True), Column("timestamp", database.UTCDateTime),
          Column("facility_id", String(64)), Column("evaluation_id", String(64)), Column("event_type", String(64)), Column("payload", JSON))
    engine = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    legacy.create_all(engine)
    added = database.add_missing_columns(engine)
    assert set(added) == {"record_events.prev_hash", "record_events.hash"}
    columns = inspect(engine).get_columns("record_events")
    assert all(c["nullable"] for c in columns if c["name"] in {"prev_hash", "hash"})
    engine.dispose()


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/audit.db", connect_args={"check_same_thread": False, "timeout": 30})
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    for module in (database, record, services, seed):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(record, "settings", replace(record.settings, snapshot_dir=tmp_path / "snapshots"))
    seed.seed()
    with factory() as db:
        yield db, factory
    engine.dispose()


def append(db, name="test", facility_id=seed.FACILITY_ID, **kwargs):
    event = record.append(db, name, {"test": "saved"}, facility_id, **kwargs)
    db.commit()
    return event


def test_chain_order_is_append_order_not_source_time_and_is_facility_scoped(audit_db):
    db, _ = audit_db
    first = append(db)
    second = append(db, timestamp=database.utcnow() - timedelta(days=30))
    other = append(db, facility_id="other")
    assert second.prev_hash == first.hash
    assert other.prev_hash == record.GENESIS
    for facility in (seed.FACILITY_ID, "other"):
        assert record.verify(db, facility)["ok"]
    client = TestClient(app)
    assert client.get(f"/api/record/verify?facility_id={seed.FACILITY_ID}").json()["ok"]
    assert client.get("/api/record/verify", headers={"X-Role": "security_desk"}).status_code == 403


def test_tampered_event_detected_exactly_and_not_repaired_on_restart(audit_db):
    db, factory = audit_db
    first, second, third = append(db), append(db), append(db)
    assert record.verify(db, seed.FACILITY_ID)["ok"]
    db.execute(update(models.RecordEvent.__table__).where(models.RecordEvent.id == second.id).values(payload={"test": "altered"}))
    db.commit()
    check = record.verify(db, seed.FACILITY_ID)
    assert not check["ok"] and check["first_broken_event_id"] == second.id
    assert record.verify(db, seed.FACILITY_ID, first.id)["ok"]
    database.init_db()
    assert not record.verify(db, seed.FACILITY_ID)["ok"]
    assert db.get(models.RecordEvent, third.id).hash == third.hash


def test_backfill_only_new_hash_columns_and_preserves_payloads(audit_db):
    db, factory = audit_db
    db.execute(insert(models.RecordEvent.__table__).values(timestamp=database.utcnow(), facility_id="legacy", event_type="old", payload={"original": "untouched"}))
    db.commit()
    database.init_db()
    db.expire_all()
    event = db.scalar(select(models.RecordEvent).where(models.RecordEvent.facility_id == "legacy"))
    assert event.payload == {"original": "untouched"}
    assert event.prev_hash == record.GENESIS
    assert record.verify(db, "legacy")["ok"]
    digest = event.hash
    database.init_db()
    assert db.get(models.RecordEvent, event.id).hash == digest


def test_concurrent_appends_never_fork_chain(audit_db):
    db, factory = audit_db
    def write(index):
        with factory() as session:
            append(session, name=f"thread-{index}", facility_id="parallel")
    with ThreadPoolExecutor(max_workers=5) as workers:
        list(workers.map(write, range(20)))
    result = record.verify(db, "parallel")
    assert result["ok"] and result["event_count"] == 20


def test_append_only_guards_remain(audit_db):
    db, _ = audit_db
    event = append(db)
    event.payload = {"test": "mutation"}
    with pytest.raises(RuntimeError, match="append-only"):
        db.commit()
    db.rollback()
    db.delete(event)
    with pytest.raises(RuntimeError, match="append-only"):
        db.commit()
    db.rollback()


def test_direct_mapped_insert_is_sealed_and_canonical_across_timezones(audit_db):
    from datetime import timezone
    db, _ = audit_db
    event = models.RecordEvent(event_type="direct", facility_id="direct", payload={"name": "María", "value": 1.5})
    db.add(event)
    db.commit()
    assert event.hash and event.prev_hash == record.GENESIS
    assert record.verify(db, "direct")["ok"]
    original = {k: getattr(event, k) for k in ("id", "timestamp", "facility_id", "evaluation_id", "event_type", "payload")}
    shifted = {**original, "timestamp": original["timestamp"].astimezone(timezone(timedelta(hours=-5)))}
    assert record.event_hash(record.GENESIS, original) == record.event_hash(record.GENESIS, shifted)


def test_snapshot_verify_hashes_actual_bytes_and_plain_text_never_executes(audit_db):
    db, _ = audit_db
    raw = b'{"untrusted":"<script>alert(1)</script>"}\n'
    digest = record.save_snapshot(raw)
    client = TestClient(app)
    result = client.get(f"/api/snapshots/{digest}/verify").json()
    assert result["match"] and result["actual_sha256"] == hashlib.sha256(raw).hexdigest()
    response = client.get(f"/api/snapshots/{digest}")
    assert response.content == raw and response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-security-policy"] == "sandbox"
    response = client.get(f"/api/snapshots/{digest}", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == f"/saved-copy/{digest}"
    snapshots.path_for(digest).write_bytes(b'changed bytes')
    result = client.get(f"/api/snapshots/{digest}/verify").json()
    assert not result["match"] and result["actual_sha256"] == hashlib.sha256(b'changed bytes').hexdigest()
    assert client.get("/api/snapshots/not-a-hash/verify").status_code == 404


def saved_recommendation(db):
    ev = schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="wind_gust", value=65, units="mph",
                          retrieved_at=database.utcnow(), status="ok", url="https://api.weather.gov/test")
    services.store_evidence(db, [ev], seed.FACILITY_ID)
    citation = {"title": "Test saved NWS criteria", "publisher": "NWS", "url": "https://www.weather.gov/eax/", "section_or_page": "High Wind Warning", "retrieved_at": "2026-10-10"}
    rec = models.Recommendation(id=uuid.uuid4().hex, facility_id=seed.FACILITY_ID, evaluated_at=database.utcnow(),
          operating_date=database.utcnow().date(), posture="closure", triggers=[{"rule_id": "high_wind", "description": "High wind", "threshold": 58, "observed": 65, "margin": 7, "units": "mph", "citation": citation}],
          confidence="high", confidence_reasons=["Current evidence covers the commute window"], what_would_change=["High Wind below 58 mph"], rationale="Wind threshold crossed", evidence_ids=[ev.id], policy_version="1.0.0+test", target_note="Today selected as a workday", lead_time_hours=1.5, scenario_kind="simulated")
    db.add(rec)
    db.commit()
    return rec, ev


def test_missing_raw_snapshot_has_honest_normalized_copy_and_metadata(audit_db):
    db, _ = audit_db
    rec, ev = saved_recommendation(db)
    row = db.get(models.Evidence, ev.id)
    assert row.snapshot_hash is None and row.saved_copy_kind == "normalized"
    copy = snapshots.metadata(db, row.saved_copy_hash)
    assert copy["match"] and copy["copies"][0]["source_url"] == ev.url
    assert b'original raw response was not stored' in snapshots.path_for(row.saved_copy_hash).read_bytes()


def test_memo_all_sections_anchor_stable_and_private_needs_omitted(audit_db, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(memo, "settings", replace(memo.settings, staffing_feature=True))
    db, _ = audit_db
    rec, ev = saved_recommendation(db)
    # Avoid message drafting completely, even mock model calls are unnecessary.
    monkeypatch.setattr("app.notify.create_notice", lambda *args: None)
    inv = models.Investigation(id=uuid.uuid4().hex, recommendation_id=rec.id, budget_key=rec.id,
         question="Why closure?", created_at=database.utcnow(), status="completed", result={})
    db.add(inv)
    record.append(db, "investigation.answer", {"answer": f"For {rec.operating_date}, winds crossed the threshold. [evidence:{ev.id}]", "source_label": "Record summary"}, rec.facility_id, evaluation_id=inv.id)
    db.commit()
    decision = services.record_decision(db, rec, schemas.DecisionIn(action="override", final_posture="telework", manager_name="Maria <script>", reason="mobility_early_dismissal for wheelchair access"))
    anchor = decision.record_hash
    notice = models.Notice(id=uuid.uuid4().hex, decision_id=decision.id, facility_id=rec.facility_id, operating_date=rec.operating_date, posture="telework", kind="initial")
    db.add(notice)
    db.flush()
    db.add(models.Notification(id=uuid.uuid4().hex, notice_id=notice.id, employee_id=seed.emp_id("ava"), included=True,
              reason_codes=["EMERGENCY_ONSITE"], wave="1", message_type="call_in", channels_planned=["push"], channel_results={"push": {"ok": True}},
              requires_ack=True, acknowledged_at=database.utcnow(), escalation_level=1, reason_text="text_only", duty_instruction="screen_reader", message="wheelchair detail"))
    append(db, "notification.test")
    assert record.verify(db, rec.facility_id)["head_hash"] != anchor
    assert db.get(models.Decision, decision.id).record_hash == anchor
    client = TestClient(app)
    response = client.get(f"/decisions/{decision.id}/memo")
    assert response.status_code == 200
    page = response.text
    for section in ("Decision and target day", "Triggers and threshold citations", "Confidence and forecast lead time", "What would have changed it", "Evidence and saved copies", "Investigations and answers", "Notification summary", "Record verification"):
        assert section in page
    for expected in ("SIMULATED", "Today selected as a workday", "Record verified", anchor, "High Wind Warning", "/thresholds#high_wind", "58", "65", "Record summary", "Call-ins", "Wave: 1", "Channel: push", "EMERGENCY ONSITE", "@media print", "Saved copy (normalized evidence)"):
        assert expected in page
    for private in ("mobility_early_dismissal", "wheelchair", "text_only", "screen_reader", "accommodation_needs", "<script>"):
        assert private not in page
    assert "Maria &lt;script&gt;" in page
    assert client.get(f"/api/decisions/{decision.id}/memo").status_code == 200
    assert client.get(f"/decisions/{decision.id}/memo", headers={"X-Role": "security_desk"}).status_code == 403
    assert client.get("/decisions/missing/memo").status_code == 404


def test_memo_displays_chain_break(audit_db, monkeypatch):
    db, _ = audit_db
    rec, ev = saved_recommendation(db)
    monkeypatch.setattr("app.notify.create_notice", lambda *args: None)
    decision = services.record_decision(db, rec, schemas.DecisionIn(action="approve", manager_name="Maria"))
    db.execute(update(models.RecordEvent.__table__).where(models.RecordEvent.id == decision.record_event_id).values(payload={"tampered": True}))
    db.commit()
    page = memo.render(db, decision)
    assert f"Record broken at event {decision.record_event_id}" in page

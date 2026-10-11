"""Notices end to end (database + API, no network): versioning (update / all clear / unchanged,
never a duplicate send), call-ins and acks, reminder/escalation timers in DEMO_MODE, supervisor
data isolation, and the rule that accommodation details never reach supervisors or managers."""
import dataclasses
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import calendar, models, schemas
from app.db import SessionLocal, utcnow
from app.main import app
from app.notify import escalation, notices
from app.notify import plan as plan_module
from app.seed import FACILITY_ID, ROSTER, emp_id, seed
from app.services import record_decision

TZ = ZoneInfo("America/Chicago")
NEEDS = ["early_notice", "mobility_early_dismissal", "text_only", "screen_reader", "large_text", "plain_language",
         "accommodation_needs", "mobility", "air_quality_sensitive"]
MANAGER = {"X-Role": "facility_manager"}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture(autouse=True)
def quiet_channels(monkeypatch):
    """Record deliveries instead of printing them."""
    sent = []
    enabled = dataclasses.replace(notices.settings, staffing_feature=True)
    monkeypatch.setattr(notices, "settings", enabled)
    monkeypatch.setattr(plan_module, "settings", enabled)
    from app import people_api
    monkeypatch.setattr(people_api, "settings", enabled)
    monkeypatch.setattr(notices, "send_push", lambda topic, t, m, ack=None: (sent.append(("push", t)) or (False, "no topic")))
    monkeypatch.setattr(notices, "send_email", lambda to, s, b, h=None: (sent.append(("email", s)) or (True, None)))
    return sent


def workday(n: int = 0):
    """The n-th operating day from today (weekends and federal holidays skipped; shifts are seeded
    for the next 8 days)."""
    day, _ = calendar.next_operating_day(datetime.now(TZ).date())
    for _ in range(n):
        day, _ = calendar.next_operating_day(day + timedelta(days=1))
    return day


def _rec(posture: str, day_offset: int = 0) -> models.Recommendation:
    day = workday(day_offset)
    with SessionLocal() as db:
        rec = models.Recommendation(
            id=uuid.uuid4().hex, facility_id=FACILITY_ID, evaluation_id=None, evaluated_at=utcnow(),
            operating_date=day, posture=posture, triggers=[], confidence="high", confidence_reasons=[],
            what_would_change=[], rationale="test", rationale_source="template", evidence_ids=[], policy_version="t")
        db.add(rec)
        db.commit()
        return rec


def _decide(posture: str, day_offset: int = 0, **params) -> tuple[models.Decision, models.Notice]:
    rec = _rec(posture, day_offset)
    with SessionLocal() as db:
        d = record_decision(db, db.get(models.Recommendation, rec.id),
                            schemas.DecisionIn(manager_name="Test Manager", action="approve", **params))
        notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == d.id))
        return d, notice


def _rows(notice_id: str) -> dict[str, models.Notification]:
    with SessionLocal() as db:
        return {f"{n.employee_id}:{n.message_type}": n for n in
                db.scalars(select(models.Notification).where(models.Notification.notice_id == notice_id))}


def _personal(rows: dict, key: str) -> models.Notification:
    eid = emp_id(key)
    return next(n for k, n in rows.items() if k.startswith(eid + ":") and n.message_type in ("action_required", "fyi", "none"))


def _send(notice_id: str):
    with SessionLocal() as db:
        return notices.send_notice(db, notice_id)


# ---------------------------------------------------------------- versioning

def test_simulated_staff_not_marked_sent_or_retried(monkeypatch):
    monkeypatch.setattr(notices, "settings", dataclasses.replace(notices.settings, demo_mode=True,
                                                               demo_email="owner@test.invalid"))
    monkeypatch.setattr(notices, "send_push", lambda *a, **k: (True, None))
    monkeypatch.setattr(notices, "send_email", lambda *a, **k: (True, None))
    _, notice = _decide("closure", day_offset=4)
    rows = _send(notice.id)
    synthetic = [n for n in rows if n.channel_results and all(r.get("status") == "simulated" for r in n.channel_results.values())]
    assert synthetic and all(n.sent_at is None for n in synthetic)
    before = {n.id: n.channel_results for n in synthetic}
    after = _send(notice.id)
    assert all(n.channel_results == before[n.id] for n in after if n.id in before)
    with TestClient(app) as client:
        response = client.get(f"/api/decisions/{notice.decision_id}/notice", headers=MANAGER)
    assert response.status_code == 200
    public = {n["id"]: n for n in response.json()["notifications"]}
    assert all(r["status"] == "simulated" and r["message"].startswith("simulated:")
               for nid in before for r in public[nid]["channel_results"].values())

def test_notice_versioning_update_all_clear_and_no_duplicates(quiet_channels):
    # v1: closure tomorrow (own date, so other tests' notices don't interfere)
    _, v1 = _decide("closure", day_offset=1)
    assert v1.version == 1 and v1.kind == "initial"
    sent_v1 = [n for n in _send(v1.id) if n.sent_at]
    assert sent_v1 and all(n.message for n in sent_v1)
    assert _personal(_rows(v1.id), "sam").duty_instruction == "office closed, telework"
    before = len(quiet_channels)
    _send(v1.id)  # idempotent: nothing is sent twice
    assert len(quiet_channels) == before

    # v2: changed decision -> update for changed instructions, with what changed
    _, v2 = _decide("delayed_arrival", day_offset=1, delay_hours=3)
    assert v2.version == 2 and v2.kind == "update" and v2.supersedes_notice_id == v1.id
    sam = _personal(_rows(v2.id), "sam")
    assert sam.change == "update" and sam.included and sam.duty_instruction == "report by 10:30 or telework"
    assert "**Report by 10:30 or telework.**" in sam.message
    assert sam.message.startswith("Update:")
    ava = _personal(_rows(v2.id), "ava")  # emergency: "report as normal and acknowledge" -> "report at your normal time..."
    assert ava.change == "update"
    _send(v2.id)

    # v3: same decision again -> every personal message unchanged, nothing resent
    _, v3 = _decide("delayed_arrival", day_offset=1, delay_hours=3)
    rows3 = _rows(v3.id)
    personal = [n for n in rows3.values() if n.message_type in ("action_required", "fyi")]
    assert personal and all(n.change == "unchanged" and not n.included for n in personal)
    before = len(quiet_channels)
    _send(v3.id)
    assert len(quiet_channels) == before  # identical messages are never resent

    # v4: back to normal -> all clear for everyone who had been told to act
    _, v4 = _decide("normal", day_offset=1)
    assert v4.kind == "all_clear"
    sam4 = _personal(_rows(v4.id), "sam")
    assert sam4.change == "all_clear" and sam4.included and "ALL_CLEAR" in sam4.reason_codes
    hannah = _personal(_rows(v4.id), "hannah")  # detail out: never told anything, no all clear
    assert not hannah.included


# ---------------------------------------------------------------- call-ins and acks

def test_call_in_flow_and_ack_page():
    d, notice = _decide("closure")
    client = TestClient(app)
    detail = client.get(f"/api/decisions/{d.id}/notice", headers=MANAGER).json()
    animal = next(c for c in detail["coverage"] if c["code"] == "animal_care")
    assert animal["label"] == "Animal care 1/2 - shortfall"
    assert [b["name"] for b in animal["backups"]] == ["Jordan Pike"]  # the one cross-trained backup

    # Not a ranked backup -> refused; the manager never assigns anyone else.
    r = client.post(f"/api/notices/{notice.id}/call-ins", headers=MANAGER,
                    json={"employee_id": emp_id("sam"), "function_code": "animal_care", "manager_name": "M"})
    assert r.status_code == 409

    r = client.post(f"/api/notices/{notice.id}/call-ins", headers=MANAGER,
                    json={"employee_id": emp_id("jordan"), "function_code": "animal_care", "manager_name": "M"})
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["message_type"] == "call_in" and n["requires_ack"] and n["wave"] == "1" and n["sent_at"]
    assert "CALL_IN" in n["reason_codes"] and "/ack/" + n["id"] in n["message"]
    animal = next(c for c in client.get(f"/api/notices/{notice.id}", headers=MANAGER).json()["coverage"]
                  if c["code"] == "animal_care")
    assert animal["status"] == "pending"

    page = client.get(f"/ack/{n['id']}")
    assert page.status_code == 200 and "recorded" in page.text
    assert f'href="/me?as={emp_id("jordan")}"' in page.text and "View my status" in page.text
    animal = next(c for c in client.get(f"/api/notices/{notice.id}", headers=MANAGER).json()["coverage"]
                  if c["code"] == "animal_care")
    assert animal["label"] == "Animal care 2/2 - covered"
    assert client.get("/ack/not-a-real-id").status_code == 404


# ---------------------------------------------------------------- reminders and escalation

def test_reminder_and_escalation_timers_in_demo_mode(monkeypatch):
    monkeypatch.setattr(escalation, "settings", dataclasses.replace(escalation.settings, demo_mode=True))
    deliveries = []
    monkeypatch.setattr(escalation, "deliver", lambda person, ch, s, b, ack=None, **kw: deliveries.append(person.id) or {})
    _, notice = _decide("closure")
    sent_at = utcnow() - timedelta(hours=1)
    with SessionLocal() as db:
        n = db.scalar(select(models.Notification).where(models.Notification.notice_id == notice.id,
                                                        models.Notification.employee_id == emp_id("ava"),
                                                        models.Notification.message_type == "action_required"))
        assert n.requires_ack
        n.sent_at = sent_at
        others = db.scalars(select(models.Notification).where(models.Notification.requires_ack.is_(True),
                                                              models.Notification.acknowledged_at.is_(None),
                                                              models.Notification.id != n.id)).all()
        for o in others:  # isolate this test from other tests' pending acks
            o.acknowledged_at = utcnow()
        db.commit()
        nid = n.id

    assert escalation.tick(sent_at + timedelta(seconds=30)) == []
    assert escalation.tick(sent_at + timedelta(seconds=61)) == ["ack.reminder_sent"]
    assert escalation.tick(sent_at + timedelta(seconds=121)) == ["ack.escalated.1"]
    assert escalation.tick(sent_at + timedelta(seconds=181)) == ["ack.escalated.2"]
    assert escalation.tick(sent_at + timedelta(minutes=10)) == []
    assert deliveries == [emp_id("ava"), emp_id("sup_animal"), emp_id("mgr")]  # person, supervisor, manager
    with SessionLocal() as db:
        n = db.get(models.Notification, nid)
        assert n.escalation_level == 2 and n.escalated_to == emp_id("mgr") and n.reminder_sent_at
        kinds = [e.event_type for e in db.scalars(select(models.RecordEvent).where(
            models.RecordEvent.event_type.in_(["ack.reminder_sent", "ack.escalated"])))
            if e.payload.get("notification_id") == nid]
    assert kinds == ["ack.reminder_sent", "ack.escalated", "ack.escalated"]


def test_ack_stops_escalation(monkeypatch):
    monkeypatch.setattr(escalation, "settings", dataclasses.replace(escalation.settings, demo_mode=True))
    monkeypatch.setattr(escalation, "deliver", lambda *a, **k: {})
    _, notice = _decide("closure")
    with SessionLocal() as db:
        for o in db.scalars(select(models.Notification).where(models.Notification.requires_ack.is_(True),
                                                              models.Notification.acknowledged_at.is_(None))):
            o.acknowledged_at = utcnow()
        n = db.scalar(select(models.Notification).where(models.Notification.notice_id == notice.id,
                                                        models.Notification.employee_id == emp_id("priya")))
        n.sent_at, n.acknowledged_at = utcnow() - timedelta(hours=1), None
        db.commit()
        notices.acknowledge(db, n.id)
    assert escalation.tick() == []


# ---------------------------------------------------------------- roles and privacy

def test_supervisor_sees_only_own_team():
    d, _ = _decide("closure")
    client = TestClient(app)
    dana = {"X-Role": "supervisor", "X-Person": emp_id("sup_animal")}
    board = client.get("/api/team", headers=dana, params={"supervisor_id": emp_id("sup_admin")}).json()
    team = {emp_id(r[0]) for r in ROSTER if r[4] == "sup_animal"}
    assert board["supervisor"]["id"] == emp_id("sup_animal")  # the query param can't widen the view
    assert {m["id"] for m in board["members"]} == team
    assert board["notifications"] and {n["employee_id"] for n in board["notifications"]} <= team
    assert all("reason_text" not in n and "message" not in n for n in board["notifications"])
    # manager-only and other-role routes are refused
    assert client.get(f"/api/decisions/{d.id}/notice", headers=dana).status_code == 403
    assert client.get(f"/api/recommendations/{d.recommendation_id}", headers=dana).status_code == 403
    security = {"X-Role": "security_desk"}
    assert client.get("/api/team", headers=security).status_code == 403
    assert client.get(f"/api/facilities/{FACILITY_ID}/access-list", headers=security).status_code == 200
    # a non-supervisor can't pose as one
    assert client.get("/api/team", headers={"X-Role": "supervisor", "X-Person": emp_id("sam")}).status_code == 403


def test_employee_sees_own_status_and_can_only_ack_own():
    d, notice = _decide("closure")
    _send(notice.id)
    client = TestClient(app)
    ava = {"X-Role": "employee", "X-Person": emp_id("ava")}
    me = client.get("/api/me", headers=ava).json()
    assert me["employee"]["id"] == emp_id("ava") and me["duty_instruction"] == "report as normal and acknowledge"
    assert me["ack_url"] and "accommodation_needs" not in me["employee"]
    other = _personal(_rows(notice.id), "priya")
    assert client.post(f"/api/notifications/{other.id}/ack", headers=ava).status_code == 403
    own = me["notification"]["id"]
    assert client.post(f"/api/notifications/{own}/ack", headers=ava).json()["acknowledged_at"]
    assert client.get("/api/me", headers={"X-Role": "employee"}).status_code == 400  # needs X-Person


def test_accommodation_details_never_in_supervisor_or_manager_responses():
    d, notice = _decide("early_dismissal")
    _send(notice.id)
    client = TestClient(app)
    responses = [
        client.get(f"/api/recommendations/{d.recommendation_id}", headers=MANAGER),
        client.get(f"/api/decisions/{d.id}/notice", headers=MANAGER),
        client.get(f"/api/notices/{notice.id}/access-list", headers=MANAGER),
        client.get("/api/viewers"),
        client.get("/api/record", headers=MANAGER),
    ] + [client.get("/api/team", headers={"X-Role": "supervisor", "X-Person": emp_id(s)})
         for s in ("sup_animal", "sup_ops", "sup_admin")] + [
        client.get("/api/team", headers=MANAGER, params={"supervisor_id": emp_id("sup_ops")})]
    for r in responses:
        assert r.status_code == 200, r.url
        text = json.dumps(r.json()).lower()
        for word in NEEDS:
            assert word not in text, f"{word!r} leaked in {r.url}"
    detail = responses[1].json()
    rhea = next(n for n in detail["notifications"] if n["employee_id"] == emp_id("rhea")
                and n["message_type"] == "action_required")
    assert "ACCOMMODATION_ON_FILE" in rhea["reason_codes"] and "accommodation on file" in rhea["reason_text"]
    assert rhea["duty_instruction"] == "leave by 13:00"  # the effect is applied, the need isn't named

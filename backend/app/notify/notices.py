"""Notices: persist a plan, send it in waves, call-ins and acknowledgments.

Nothing is sent without a Decision: a Notice is created from one, and send_notice() refuses
anything else. Identical messages are never resent: unchanged recipients of a superseding notice
are stored with change="unchanged" and not sent.
"""
import threading
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, record, schemas
from ..access.policy import Access
from ..config import settings
from ..db import utcnow
from . import exposure as exposure_mod
from . import instructions as ins
from . import messages as msg
from .channels import send_email, send_push
from .delivery import channels_for
from .impact import fmt
from .plan import (PERSONAL, WAVE_ORDER, Plan, Planned, Previous, ack_url, build_plan, context_for, load_inputs,
                   signature)
from ..staffing.coverage import CallIn

_send_lock = threading.RLock()


class NoDecisionError(Exception):
    """Raised when something tries to send without an approval record."""


class CallInError(ValueError):
    pass


# ---------------------------------------------------------------- building

def previous_map(db: Session, facility_id: str, day) -> dict[tuple[str, str], Previous]:
    """What each person was last actually sent for this facility and date, across notice versions."""
    rows = db.execute(select(models.Notification, models.Notice).join(
        models.Notice, models.Notification.notice_id == models.Notice.id).where(
        models.Notice.facility_id == facility_id, models.Notice.operating_date == day,
        models.Notice.archived_at.is_(None), models.Notification.archived_at.is_(None),
        models.Notification.sent_at.is_not(None), models.Notification.message_type != "call_in")
        .order_by(models.Notice.version)).all()
    out: dict[tuple[str, str], Previous] = {}
    for n, _ in rows:
        if n.message_type in PERSONAL:
            fam, sig = "personal", signature(n.message_type, n.duty_instruction, n.building_access)
        else:
            fam, sig = n.message_type, signature(n.message_type, (n.message or "").split("\n", 1)[-1].rsplit("\n", 1)[0])
        out[(n.employee_id, fam)] = Previous(n.message_type, sig, n.duty_instruction, n.sent_at)
    return out


def latest_sent_notice(db: Session, facility_id: str, day) -> models.Notice | None:
    return db.scalar(select(models.Notice).where(models.Notice.facility_id == facility_id,
                                                 models.Notice.operating_date == day,
                                                 models.Notice.archived_at.is_(None),
                                                 models.Notice.sent_at.is_not(None))
                     .order_by(models.Notice.version.desc()).limit(1))


def _call_ins(db: Session, notice_id: str) -> list[CallIn]:
    return [CallIn(n.id, n.employee_id, n.function_code or "", n.acknowledged_at is not None)
            for n in db.scalars(select(models.Notification).where(models.Notification.notice_id == notice_id,
                                                                  models.Notification.message_type == "call_in"))]


def plan_for(db: Session, facility: models.Facility, rec: models.Recommendation, posture: str, *,
             delay_hours: int | None = None, dismissal_time: str | None = None, now: datetime | None = None,
             previous: dict | None = None, call_ins: list[CallIn] = ()) -> Plan:
    now = now or utcnow()
    if rec.scenario_context and rec.scenario_context.get("clock"):
        now = datetime.fromisoformat(rec.scenario_context["clock"])
    people, assignments, shifts, functions, names = load_inputs(db, facility, rec.operating_date)
    demo = rec.target_mode == "demo_workday"
    if demo:  # DEMO: today is evaluated as a workday, so use the weekday schedule for it
        from ..seed import roster_shifts

        shifts = [s for s in shifts if s.date != rec.operating_date] + roster_shifts(rec.operating_date, workday=True)
    ctx = context_for(facility, rec.operating_date, posture, now, functions, delay_hours, dismissal_time,
                      demo_workday=demo)
    exposure = exposure_mod.load(db, facility.id, rec.evaluation_id, now, evidence_ids=rec.evidence_ids)
    return build_plan(ctx, people, assignments, shifts, functions, exposure, base_url=settings.public_base_url,
                      previous=previous, call_ins=call_ins, facility_names=names)


def plan_for_notice(db: Session, notice: models.Notice, now: datetime | None = None) -> Plan:
    decision = db.get(models.Decision, notice.decision_id)
    rec = db.get(models.Recommendation, decision.recommendation_id)
    facility = db.get(models.Facility, notice.facility_id)
    return plan_for(db, facility, rec, notice.posture, delay_hours=decision.delay_hours,
                    dismissal_time=decision.dismissal_time, now=now, call_ins=_call_ins(db, notice.id))


def create_notice(db: Session, decision: models.Decision, now: datetime | None = None) -> models.Notice:
    """Plan and draft every message for a decision (not sent yet). One notice per decision."""
    existing = db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    if existing:
        return existing
    from ..agents import message_agent  # agents import notify; avoid the cycle at import time

    rec = db.get(models.Recommendation, decision.recommendation_id)
    facility = db.get(models.Facility, rec.facility_id)
    prev = latest_sent_notice(db, facility.id, rec.operating_date)
    plan = plan_for(db, facility, rec, decision.final_posture, delay_hours=decision.delay_hours,
                    dismissal_time=decision.dismissal_time, now=now,
                    previous=previous_map(db, facility.id, rec.operating_date))
    version = prev.version + 1 if prev else 1
    notice = models.Notice(
        id=uuid.uuid4().hex, decision_id=decision.id, facility_id=facility.id, operating_date=rec.operating_date,
        version=version, supersedes_notice_id=prev.id if prev else None,
        kind="initial" if not prev else "all_clear" if decision.final_posture == "normal" else "update",
        posture=decision.final_posture, params=plan.ctx.params(), created_at=utcnow())
    db.add(notice)

    # Wording: drafts from recommendation time are reused; the LLM is called again only for an
    # override or a new notice version (and only for groups not drafted yet).
    stats = message_agent.fill_messages(
        plan, allow_llm=not rec.scenario_kind and (decision.action == "override" or version > 1),
        evaluation_id=rec.evaluation_id, facility_id=facility.id, budget_key=f"notice:{notice.id}",
        wait_for_predraft=rec.id)
    for p in plan.planned:
        if rec.scenario_kind and p.included:
            label = "SIMULATED" if rec.scenario_kind == "simulated" else "REPLAY (not current conditions)"
            p.subject, p.message = f"{label}: {p.subject}", f"{label}\n{p.message}"
        db.add(_row(p, notice.id))
    record.append(db, "notice.created", {
        "notice_id": notice.id, "decision_id": decision.id, "version": version, "kind": notice.kind,
        "supersedes_notice_id": notice.supersedes_notice_id, "posture": notice.posture, "params": notice.params,
        "to_send": sum(1 for p in plan.planned if p.included),
        "unchanged": sum(1 for p in plan.planned if p.change == "unchanged"),
        "decision_clock": plan.clock.model_dump(mode="json"), "drafting": stats,
        "coverage": [c.label for c in plan.coverage],
    }, facility_id=facility.id, evaluation_id=rec.evaluation_id)
    db.commit()
    return notice


def _row(p: Planned, notice_id: str) -> models.Notification:
    return models.Notification(
        id=p.id, notice_id=notice_id, employee_id=p.employee.id, included=p.included,
        reason_codes=p.reason_codes, reason_text=p.reason_text, audience_text=p.audience_text,
        duty_instruction=p.duty_instruction, building_access=p.access.label if p.access else "",
        access_code=p.access.code if p.access else "", notify_by=p.notify_by, wave=p.wave,
        message_type=p.message_type, change=p.change, requires_ack=p.requires_ack, function_code=p.function_code,
        channels_planned=p.channels, channel_results={}, subject=p.subject if p.included else None,
        message=p.message if p.included else None, drafted_by=p.drafted_by if p.included else None)


# ---------------------------------------------------------------- sending

def deliver(person: models.Employee, channels: list[str], subject: str, body: str, *,
            ack: str | None = None) -> dict:
    """Send on the planned channels. If push fails and email wasn't planned, email immediately.
    Returns {channel: {sent_at, ok, error, fallback}} for the record."""
    render = frozenset(person.accommodation_needs or [])
    results: dict[str, dict] = {}

    def one(ch: str, fallback: bool = False):
        if ch == "push":
            ok, err = send_push(person.ntfy_topic, subject, body, ack)
        else:
            ok, err = send_email(person.email, subject, body, msg.html_body(body, render))
        results[ch] = {"sent_at": utcnow().isoformat(), "ok": ok, "error": err, "fallback": fallback}

    for ch in channels:
        one(ch)
    if "push" in results and not results["push"]["ok"] and "email" not in results:
        one("email", fallback=True)
    return results


def send_notice(db: Session, notice_id: str) -> list[models.Notification]:
    """Send every included, unsent notification: wave 1, then 2, then FYI. Idempotent."""
    with _send_lock:
        notice = db.get(models.Notice, notice_id)
        if notice is None or notice.archived_at is not None or db.get(models.Decision, notice.decision_id) is None:
            raise NoDecisionError(f"no decision for notice {notice_id}; nothing is sent without an approval record")
        rows = list(db.scalars(select(models.Notification).where(models.Notification.notice_id == notice_id)))
        todo = sorted((n for n in rows if n.included and n.sent_at is None and n.message),
                      key=lambda n: (WAVE_ORDER.get(n.wave, 3), n.notify_by.timestamp() if n.notify_by else float("inf"), n.id))
        people = {e.id: e for e in db.scalars(select(models.Employee).where(
            models.Employee.id.in_([n.employee_id for n in todo])))}
        rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
        for n in todo:
            n.channel_results = deliver(people[n.employee_id], n.channels_planned, n.subject or "Closure Call",
                                        n.message, ack=ack_url(settings.public_base_url, n.id) if n.requires_ack else None)
            n.sent_at = utcnow()
            record.append(db, "notification.sent", {
                "notice_id": notice_id, "notification_id": n.id, "employee_id": n.employee_id,
                "name": people[n.employee_id].name, "message_type": n.message_type, "wave": n.wave,
                "change": n.change, "reason_codes": n.reason_codes, "drafted_by": n.drafted_by,
                "channels": {k: {"ok": v["ok"], "error": v["error"], "fallback": v.get("fallback", False)}
                             for k, v in n.channel_results.items()},
            }, facility_id=notice.facility_id, evaluation_id=rec.evaluation_id)
        if notice.sent_at is None:
            notice.sent_at = utcnow()
            record.append(db, "notice.sent", {
                "notice_id": notice_id, "version": notice.version, "sent": len(todo),
                "not_sent_unchanged": sum(1 for n in rows if n.change == "unchanged"),
                "by_wave": {w: sum(1 for n in todo if n.wave == w) for w in ("1", "2", "fyi")},
            }, facility_id=notice.facility_id, evaluation_id=rec.evaluation_id)
        db.commit()
        db.expire_all()
        return list(db.scalars(select(models.Notification).where(models.Notification.notice_id == notice_id)))


# ---------------------------------------------------------------- call-ins

def call_in(db: Session, notice_id: str, body: schemas.CallInIn, now: datetime | None = None) -> models.Notification:
    """The manager picked a backup for a shortfall: send a call-in requiring acknowledgment."""
    if db.get(models.Notice, notice_id) and db.get(models.Notice, notice_id).archived_at:
        raise NoDecisionError("This demo notice was reset")
    notice = db.get(models.Notice, notice_id)
    if notice is None:
        raise NoDecisionError(f"no notice {notice_id}")
    if not body.manager_name.strip():
        raise CallInError("manager_name is required")
    plan = plan_for_notice(db, notice, now)
    item = next((c for c in plan.coverage if c.code == body.function_code), None)
    if item is None:
        raise CallInError(f"{body.function_code} is not a critical function here")
    if body.employee_id not in {b.employee_id for b in item.backups}:
        raise CallInError("not a ranked backup for this function (qualified, not on leave, not exposed)")
    if db.scalar(select(models.Notification).where(models.Notification.notice_id == notice_id,
                                                   models.Notification.employee_id == body.employee_id,
                                                   models.Notification.message_type == "call_in")):
        raise CallInError("already called in for this notice")
    person = db.get(models.Employee, body.employee_id)
    imp = next(i for i in plan.impacts if i.employee.id == body.employee_id)
    ctx = plan.ctx
    start = imp.shift.start if imp.shift and not imp.shift.starts_previous_day else (
        ctx.facility_report_dt if ctx.posture == "delayed_arrival" else ctx.open_dt)
    report = fmt(start)
    instruction = ins.text("call_in", person.language, facility_name=ctx.facility_name,
                           function_name=item.name.lower(), report_time=report)
    access = Access("allowed", None, "ACCESS_CALL_IN", "called in by the manager for a critical function")
    nid = uuid.uuid4().hex
    facts = msg.MessageFacts(
        name=person.name, language=person.language, facility_name=ctx.facility_name,
        operating_date=ctx.operating_date, posture=ctx.posture, delay_hours=int(ctx.delay.total_seconds() // 3600),
        message_type="call_in", change="initial", instruction=instruction, access=access,
        why_codes=["CALL_IN"], requires_ack=True, ack_url=ack_url(settings.public_base_url, nid))
    subject, text = msg.template(facts)
    rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
    if rec.scenario_kind:
        label = "SIMULATED" if rec.scenario_kind == "simulated" else "REPLAY (not current conditions)"
        subject, text = f"{label}: {subject}", f"{label}\n{text}"
    pub = schemas.Employee.model_validate(person)
    n = models.Notification(
        id=nid, notice_id=notice_id, employee_id=person.id, included=True,
        reason_codes=["CALL_IN", "STAFFING_SHORTFALL"],
        reason_text=f"called in by {body.manager_name.strip()} for {item.name} ({item.label})",
        audience_text=f"called in for {item.name}", duty_instruction=instruction, building_access=access.label,
        access_code=access.code, notify_by=start - timedelta(minutes=person.commute_minutes + 30), wave="1",
        message_type="call_in", change="initial", requires_ack=True, function_code=item.code,
        channels_planned=channels_for(pub, "call_in", (now or utcnow()).astimezone(ctx.tz)),
        subject=subject, message=text, drafted_by="template")
    db.add(n)
    db.flush()
    n.channel_results = deliver(person, n.channels_planned, subject, text, ack=facts.ack_url)
    n.sent_at = utcnow()
    rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
    record.append(db, "call_in.sent", {
        "notice_id": notice_id, "notification_id": nid, "employee_id": person.id, "name": person.name,
        "function": item.code, "manager_name": body.manager_name.strip(), "coverage_before": item.label,
        "channels": {k: {"ok": v["ok"], "error": v["error"]} for k, v in n.channel_results.items()},
    }, facility_id=notice.facility_id, evaluation_id=rec.evaluation_id)
    db.commit()
    return n


# ---------------------------------------------------------------- acknowledgments

def acknowledge(db: Session, notification_id: str) -> models.Notification | None:
    if db.get(models.Notification, notification_id) and db.get(models.Notification, notification_id).archived_at:
        return None
    n = db.get(models.Notification, notification_id)
    if n is None:
        return None
    if n.acknowledged_at is None:
        n.acknowledged_at = utcnow()
        notice = db.get(models.Notice, n.notice_id)
        record.append(db, "ack.received", {
            "notification_id": n.id, "notice_id": n.notice_id, "employee_id": n.employee_id,
            "message_type": n.message_type, "escalation_level": n.escalation_level,
            "seconds_after_send": int((n.acknowledged_at - n.sent_at).total_seconds()) if n.sent_at else None,
        }, facility_id=notice.facility_id if notice else None)
        db.commit()
    return n

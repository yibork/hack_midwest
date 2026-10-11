"""Notices: persist a plan, send it in waves, call-ins and acknowledgments.

Nothing is sent without a Decision: a Notice is created from one, and send_notice() refuses
anything else. Identical messages are never resent: unchanged recipients of a superseding notice
are stored with change="unchanged" and not sent.
"""
import threading
import uuid
import re
from zoneinfo import ZoneInfo
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
    for n, notice in rows:
        if n.message_type in PERSONAL:
            fam, sig = "personal", signature(n.message_type, n.duty_instruction, n.building_access)
        else:
            fam, sig = n.message_type, signature(n.message_type, (n.message or "").split("\n", 1)[-1].rsplit("\n", 1)[0])
        out[(n.employee_id, fam)] = Previous(n.message_type, sig, n.duty_instruction, n.sent_at)
        person = db.get(models.Employee, n.employee_id)
        if n.message_type == "supervisor_summary" and person.role == "facility_manager" and n.duty_instruction:
            personal_type = "fyi" if notice.posture == "normal" or "ALL_CLEAR" in n.reason_codes else "action_required"
            out[(n.employee_id, "personal")] = Previous(personal_type,
                signature(personal_type, n.duty_instruction, n.building_access), n.duty_instruction, n.sent_at)
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
    exposure = exposure_mod.load(db, facility.id, rec.evaluation_id, now, evidence_ids=rec.evidence_ids,
                                 target_day=rec.operating_date)
    plan = build_plan(ctx, people, assignments, shifts, functions, exposure, base_url=settings.public_base_url,
                      previous=previous, call_ins=call_ins, facility_names=names)
    evidence = list(db.scalars(select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids or []))))
    from ..rules.engine import load_policy
    rule_map = {r["id"]: r for r in load_policy().rules}
    for p in plan.planned:
        if p.facts:
            decorate_facts(db, facility, rec, p.facts, evidence=evidence, rule_map=rule_map)
    return plan


def short_name(facility):
    place = (facility.address or "").removeprefix("Synthetic facility relocated to ").split(";")[0]
    place = place.replace(" (synthetic demo facility)", "").replace(",", "")
    return "USDA demo office, " + (place or facility.name)


def decorate_facts(db, facility, rec, facts, *, evidence=None, rule_map=None):
    """Use only the saved recommendation's evidence, not today's live alert feed."""
    facts.facility_name = short_name(facility) if settings.demo_mode else facility.name
    facts.timezone = facility.timezone
    rows = list(evidence) if evidence is not None else list(db.scalars(
        select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids or []))))
    trigger_ids = {t.get("evidence_id") for t in rec.triggers or []}
    rows.sort(key=lambda e: (e.id not in trigger_ids, e.id))
    alerts = [(e, a) for e in rows if e.kind == "active_alerts" and e.status == "ok" and isinstance(e.value, dict)
              for a in e.value.get("alerts", []) if a.get("event")]
    from ..rules.engine import load_policy
    rules = rule_map if rule_map is not None else {r["id"]: r for r in load_policy().rules}
    triggered_events = {event for t in rec.triggers or []
                        for clause in rules.get(t["rule_id"], {}).get("condition", {}).get("any", [])
                        if clause.get("type") == "alert" for event in clause.get("events", [])}
    severity = {"Extreme": 0, "Severe": 1, "Moderate": 2}
    alerts.sort(key=lambda pair: (pair[1]["event"] not in triggered_events,
                                 pair[0].id not in trigger_ids, severity.get(pair[1].get("severity"), 3)))
    if alerts:
        e, alert = alerts[0]
        facts.hazard, facts.hazard_source = alert["event"], "NWS"
        end = alert.get("ends") or alert.get("expires")
        if end:
            try:
                local = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(ZoneInfo(facility.timezone))
                facts.hazard_end = local.strftime("%I:%M %p %Z").lstrip("0")
                if local.date() != facts.operating_date:
                    facts.hazard_end += local.strftime(" (%a %b %d)")
            except (ValueError, TypeError):
                pass  # Do not invent an expiry when the saved source does not provide one.
    elif rec.triggers:
        trigger = rec.triggers[0]
        rule = str(trigger.get("rule_id", trigger.get("rule", "")))
        if "wind" in rule or "gust" in rule:
            facts.hazard, facts.hazard_source = "Wind forecast", "NWS"
        elif "aqi" in rule or "air_quality" in rule:
            facts.hazard, facts.hazard_source = "Air quality", "EPA AirNow"
        elif "river" in rule or "flood" in rule:
            facts.hazard, facts.hazard_source = "River flooding", "NOAA"
        elif "hazard" in rule:
            facts.hazard, facts.hazard_source = "Reported site hazard", "Facility operations"
        elif "access" in rule:
            facts.hazard, facts.hazard_source = "Access route closure", "Facility evidence"
    if settings.demo_mode:
        kind = "SIMULATED alert" if rec.scenario_kind == "simulated" else "REPLAY alert (not current)" if rec.scenario_kind == "replay" else "real NWS alert" if alerts else "saved facility evidence"
        if facts.lang == "es":
            kind = {"SIMULATED alert": "alerta simulada (SIMULATED)", "REPLAY alert (not current)": "alerta grabada (REPLAY, no actual)",
                    "real NWS alert": "alerta real del NWS", "saved facility evidence": "evidencia guardada de la oficina"}[kind]
        facts.footer = ("Demo: oficina sintética, " if facts.lang == "es" else "Demo: synthetic office, ") + kind


def manager_digest(plan, hazard, sent=0, acknowledged=0):
    lines = [f"{short_name_for_plan(plan)}: {ins.posture_label(plan.ctx.posture)}, {msg.fmt_date(plan.ctx.operating_date, 'en')}. {hazard}.",
             f"Sent: {sent}. Acknowledged: {acknowledged}."]
    shortfalls = [c for c in plan.coverage if c.status != "covered"]
    for c in shortfalls:
        backup = f" Suggested backup: {c.backups[0].name} - call them in on the Decide page." if c.backups else " No eligible backup; arrange coverage."
        have = len(c.available) + sum(x.acknowledged for x in c.called_in)
        lines.append(f"{c.name} needs {c.required} on site; {have} available.{backup}")
    if not shortfalls:
        lines.append("All critical functions covered.")
    lines.append(f"Review and decide: {settings.public_base_url}/decide")
    return "\n".join(lines)


def short_name_for_plan(plan):
    return next((p.facts.facility_name for p in plan.planned if p.facts), plan.ctx.facility_name)


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
    managers = {p.employee.id for p in plan.planned if p.employee.role == "facility_manager"}
    for manager in managers:
        items = [p for p in plan.planned if p.employee.id == manager and p.included]
        if not items:
            continue
        keeper = next((p for p in items if p.message_type == "supervisor_summary"), items[0])
        keeper.channels = list(dict.fromkeys(ch for item in items for ch in item.channels))
        keeper.wave = min((item.wave for item in items), key=lambda wave: WAVE_ORDER.get(wave, 3))
        keeper.notify_by = min((item.notify_by for item in items if item.notify_by), default=None)
        first = next((p.facts for p in plan.planned if p.facts), None)
        hazard = f"{first.hazard_source}: {first.hazard}" + (f" until {first.hazard_end}" if first.hazard_end else "") if first else "Facility decision"
        keeper.subject = "Office decision - delivery and staffing summary"
        keeper.message = manager_digest(plan, hazard) + ("\n" + first.footer if first and first.footer else "")
        keeper.message_type, keeper.drafted_by = "supervisor_summary", "template"
        personal = next((p for p in plan.planned if p.employee.id == manager and p.facts), None)
        if personal:
            keeper.duty_instruction, keeper.access = personal.duty_instruction, personal.access
            keeper.requires_ack = personal.requires_ack
            keeper.reason_codes = list(dict.fromkeys(keeper.reason_codes + personal.reason_codes))
            keeper.message += "\n" + msg.personal_action(personal.facts)
            if keeper.requires_ack:
                keeper.message += f"\nPlease confirm: {ack_url(settings.public_base_url, keeper.id)}"
        previous_digest = db.scalar(select(models.Notification).join(models.Notice).where(
            models.Notice.facility_id == facility.id, models.Notice.operating_date == rec.operating_date,
            models.Notice.archived_at.is_(None), models.Notification.archived_at.is_(None),
            models.Notification.employee_id == manager, models.Notification.message_type == "supervisor_summary",
            models.Notification.sent_at.is_not(None)).order_by(models.Notification.sent_at.desc()).limit(1))
        def digest_content(body):
            body = re.sub(r"Sent: \d+\. Acknowledged: \d+\.", "Sent: 0. Acknowledged: 0.", body or "")
            return re.sub(r"Please confirm: \S+", "Please confirm: <ack link>", body)
        if previous_digest and digest_content(previous_digest.message) == digest_content(keeper.message):
            keeper.included, keeper.change = False, "unchanged"
        for other in items:
            if other is not keeper:
                other.included = False
                other.reason_text += "; included in manager digest"
    for p in plan.planned:
        if rec.scenario_kind and p.included and p.message_type not in (*PERSONAL, "supervisor_summary"):
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
            ack: str | None = None, allow_email_fallback: bool = True) -> dict:
    """Send on the planned channels. If push fails and email wasn't planned, email immediately.
    Returns {channel: {sent_at, ok, error, fallback}} for the record."""
    render = frozenset(person.accommodation_needs or [])
    results: dict[str, dict] = {}
    from ..seed import PERSONAS, emp_id
    persona = settings.demo_mode and person.id in {emp_id(key) for key in PERSONAS}

    def one(ch: str, fallback: bool = False):
        if settings.demo_mode and ((ch == "push" and not person.ntfy_topic) or (ch == "email" and not persona)):
            reason = "simulated: synthetic employee" + (", no device" if ch == "push" else "")
            results[ch] = {"sent_at": None, "attempted_at": utcnow().isoformat(), "ok": False,
                           "status": "simulated", "message": reason, "error": None, "fallback": fallback}
            return
        if ch == "push":
            ok, err = send_push(person.ntfy_topic, subject, body, ack)
        elif settings.demo_mode:
            if not settings.demo_email:
                ok, err = False, "DEMO_EMAIL is not configured"
            else:
                # The structured body already labels the synthetic demo. No extra greeting/status lines.
                ok, err = send_email(settings.demo_email, subject,
                                     body, msg.html_body(body, render), demo=True)
        else:
            ok, err = send_email(person.email, subject, body, msg.html_body(body, render))
        results[ch] = {"sent_at": utcnow().isoformat() if ok else None, "attempted_at": utcnow().isoformat(),
                       "ok": ok, "status": "delivered" if ok else "failed", "error": str(err) if err else None,
                       "http_status": getattr(err, "http_status", None), "fallback": fallback}

    for ch in channels:
        one(ch)
    if allow_email_fallback and "push" in results and results["push"]["status"] == "failed" and "email" not in results:
        one("email", fallback=True)
    return results


def send_notice(db: Session, notice_id: str) -> list[models.Notification]:
    """Send every included, unsent notification: wave 1, then 2, then FYI. Idempotent."""
    with _send_lock:
        notice = db.get(models.Notice, notice_id)
        if notice is None or notice.archived_at is not None or db.get(models.Decision, notice.decision_id) is None:
            raise NoDecisionError(f"no decision for notice {notice_id}; nothing is sent without an approval record")
        rows = list(db.scalars(select(models.Notification).where(models.Notification.notice_id == notice_id)))
        todo = sorted((n for n in rows if n.included and n.sent_at is None and n.message
                       and not (n.channel_results and all(v.get("status") == "simulated" for v in n.channel_results.values()))),
                      key=lambda n: (WAVE_ORDER.get(n.wave, 3), n.notify_by.timestamp() if n.notify_by else float("inf"), n.id))
        people = {e.id: e for e in db.scalars(select(models.Employee).where(
            models.Employee.id.in_([n.employee_id for n in todo])))}
        rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
        todo.sort(key=lambda n: people[n.employee_id].role == "facility_manager")
        for n in todo:
            if people[n.employee_id].role == "facility_manager" and n.message_type == "supervisor_summary":
                plan = plan_for_notice(db, notice)
                first = next((p.facts for p in plan.planned if p.facts), None)
                hazard = f"{first.hazard_source}: {first.hazard}" + (f" until {first.hazard_end}" if first.hazard_end else "") if first else "Facility decision"
                others = [r for r in rows if r.employee_id != n.employee_id and r.included]
                n.message = manager_digest(plan, hazard, len({r.employee_id for r in others if r.sent_at}),
                                           len({r.employee_id for r in others if r.acknowledged_at}))
                if first and first.footer:
                    n.message += "\n" + first.footer
                personal = next((p.facts for p in plan.planned if p.employee.id == n.employee_id and p.facts), None)
                if personal:
                    n.message += "\n" + msg.personal_action(personal)
                if n.requires_ack:
                    n.message += f"\nPlease confirm: {ack_url(settings.public_base_url, n.id)}"
            n.channel_results = deliver(people[n.employee_id], n.channels_planned, n.subject or "Closure Call",
                                        n.message, ack=ack_url(settings.public_base_url, n.id) if n.requires_ack else None)
            n.sent_at = utcnow() if any(v["ok"] for v in n.channel_results.values()) else None
            event_type = "notification.sent" if n.sent_at else (
                "notification.simulated" if all(v["status"] == "simulated" for v in n.channel_results.values())
                else "notification.failed")
            record.append(db, event_type, {
                "notice_id": notice_id, "notification_id": n.id, "employee_id": n.employee_id,
                "name": people[n.employee_id].name, "message_type": n.message_type, "wave": n.wave,
                "change": n.change, "reason_codes": n.reason_codes, "drafted_by": n.drafted_by,
                "channels": n.channel_results,
            }, facility_id=notice.facility_id, evaluation_id=rec.evaluation_id)
        if notice.sent_at is None:
            notice.sent_at = utcnow()
            record.append(db, "notice.sent", {
                "notice_id": notice_id, "version": notice.version, "sent": sum(n.sent_at is not None for n in todo),
                "simulated": sum(v.get("status") == "simulated" for n in todo for v in n.channel_results.values()),
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
        why_codes=["CALL_IN"], requires_ack=True, ack_url=ack_url(settings.public_base_url, nid),
        function_name=item.name.lower(), report_time=report)
    rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
    decorate_facts(db, db.get(models.Facility, notice.facility_id), rec, facts)
    subject, text = msg.template(facts)
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
    n.sent_at = utcnow() if any(v["ok"] for v in n.channel_results.values()) else None
    rec = db.get(models.Recommendation, db.get(models.Decision, notice.decision_id).recommendation_id)
    record.append(db, "call_in.sent", {
        "notice_id": notice_id, "notification_id": nid, "employee_id": person.id, "name": person.name,
        "function": item.code, "manager_name": body.manager_name.strip(), "coverage_before": item.label,
        "channels": n.channel_results,
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

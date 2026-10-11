"""Role-aware routes: notices, access list, call-ins, team board, my status, acks, demo personas.

Data is filtered by role here, server side (see roles.py). Supervisors only ever get their own
team, with audience_text but no reason details or message bodies. Accommodation needs are never
returned by any route; EmployeePublic doesn't have the field.
"""
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, record, schemas
from .access.policy import citation as access_citation
from .access.policy import Access
from .config import settings
from .db import get_db, utcnow
from .notify import NoDecisionError, acknowledge, call_in
from .notify.impact import Exposure
from .notify import messages as msg
from .notify.notices import CallInError, decorate_facts, plan_for, plan_for_notice, short_name
from .notify.plan import ack_url, build_plan, context_for, load_inputs
from .roles import Viewer, manager_only, require, viewer
from .seed import PERSONAS, emp_id

router = APIRouter(prefix="/api")
EXPOSURE_CODES = {"HOME_IN_WARNING_AREA", "ROUTE_CLOSED"}


# ---------------------------------------------------------------- helpers

def facility_of(db: Session, person_id: str) -> models.Facility:
    from .demo import settings as demo_settings
    if demo_settings.demo_mode:
        current = db.get(models.DemoState, "usda-kc-demo")
        if current and current.active_facility_id and db.scalar(select(models.Assignment.id).where(
                models.Assignment.employee_id == person_id, models.Assignment.facility_id == current.active_facility_id).limit(1)):
            return db.get(models.Facility, current.active_facility_id)
    rows = db.scalars(select(models.Assignment).where(models.Assignment.employee_id == person_id)).all()
    pick = next((a for a in rows if a.kind == "detail_in"), None) or next((a for a in rows if a.kind == "permanent"), None)
    facility = db.get(models.Facility, pick.facility_id) if pick else None
    if facility is None:  # permanent office elsewhere (not modeled): use the first facility
        facility = db.scalar(select(models.Facility).where(models.Facility.temporary.is_(False)).limit(1))
    return facility


def current_notice(db: Session, facility_id: str, sent_only: bool = False) -> models.Notice | None:
    q = select(models.Notice).where(models.Notice.facility_id == facility_id, models.Notice.archived_at.is_(None))
    if sent_only:
        q = q.where(models.Notice.sent_at.is_not(None))
    return db.scalar(q.order_by(models.Notice.created_at.desc()).limit(1))


def latest_recommendation(db: Session, facility_id: str) -> models.Recommendation | None:
    from .demo import active_recommendation
    active = active_recommendation(db, facility_id)
    if active:
        return active
    return db.scalar(select(models.Recommendation).where(models.Recommendation.facility_id == facility_id)
                     .where(models.Recommendation.archived_at.is_(None))
                     .order_by(models.Recommendation.evaluated_at.desc()).limit(1))


def normal_plan(db: Session, facility: models.Facility):
    """Normal operations today (before any decision)."""
    now = utcnow()
    day = now.astimezone(ZoneInfo(facility.timezone)).date()
    people, assignments, shifts, functions, names = load_inputs(db, facility, day)
    ctx = context_for(facility, day, "normal", now, functions)
    return build_plan(ctx, people, assignments, shifts, functions, Exposure(), base_url=settings.public_base_url,
                      facility_names=names)


def live_plan(db: Session, facility: models.Facility):
    """No notice yet: plan for the latest recommendation's posture, or normal operations today."""
    rec = latest_recommendation(db, facility.id)
    return plan_for(db, facility, rec, rec.posture) if rec else normal_plan(db, facility)


def _transient(plan) -> list[models.Notification]:
    """Planned notifications as unsaved rows (for boards shown before any decision)."""
    return [models.Notification(
        id=p.id, notice_id="", employee_id=p.employee.id, included=p.included, reason_codes=p.reason_codes,
        reason_text=p.reason_text, audience_text=p.audience_text, duty_instruction=p.duty_instruction,
        building_access=p.access.label if p.access else "", notify_by=p.notify_by, wave=p.wave,
        message_type=p.message_type, change=p.change, requires_ack=p.requires_ack, channels_planned=p.channels,
        channel_results={}, escalation_level=0) for p in plan.planned]


def notice_detail(db: Session, notice: models.Notice) -> schemas.NoticeDetail:
    plan = plan_for_notice(db, notice)
    rows = db.scalars(select(models.Notification).where(models.Notification.notice_id == notice.id)).all()
    names = {e.id: e.name for e in db.scalars(select(models.Employee).where(
        models.Employee.id.in_({n.employee_id for n in rows})))}
    prev = db.get(models.Notice, notice.supersedes_notice_id) if notice.supersedes_notice_id else None
    return schemas.NoticeDetail(
        notice=schemas.Notice.model_validate(notice), previous=schemas.Notice.model_validate(prev) if prev else None,
        clock=plan.clock, coverage=plan.coverage, notifications=[schemas.Notification.model_validate(n) for n in rows],
        names=names)


def _get_notice(db: Session, notice_id: str) -> models.Notice:
    notice = db.get(models.Notice, notice_id)
    if notice is None:
        raise HTTPException(404, f"notice {notice_id} not found")
    return notice


def _access_list(facility: models.Facility, plan, notice: models.Notice | None) -> schemas.AccessList:
    return schemas.AccessList(facility_id=facility.id, facility_name=facility.name,
                              operating_date=plan.ctx.operating_date, posture=plan.ctx.posture,
                              notice_id=notice.id if notice else None, policy=access_citation(),
                              rows=plan.access_rows)


# ---------------------------------------------------------------- manager: notices and call-ins

def status_lines(db, facility, plan, person, n=None):
    imp = next((i for i in plan.impacts if i.employee.id == person.id), None)
    item = next((p for p in plan.planned if p.employee.id == person.id and p.facts), None)
    if not imp:
        return []
    facts = item.facts if item else msg.MessageFacts(
        name=person.name, language=person.language,
        facility_name=short_name(facility) if settings.demo_mode else facility.name,
        operating_date=plan.ctx.operating_date, posture=plan.ctx.posture, delay_hours=0,
        message_type=imp.message_type, change="initial", instruction=imp.duty_instruction,
        access=imp.access or Access("allowed", None, "", ""), why_codes=imp.codes,
        report_time=imp.shift.start.strftime("%H:%M") if imp.shift else "")
    if n:
        saved_notice = db.get(models.Notice, n.notice_id)
        decision = db.get(models.Decision, saved_notice.decision_id)
        rec = db.get(models.Recommendation, decision.recommendation_id)
        facts.instruction, facts.why_codes, facts.posture = n.duty_instruction, n.reason_codes, saved_notice.posture
        facts.change, facts.message_type = n.change, n.message_type
        verb, _, clock = n.building_access.partition(" ")
        facts.access = Access(verb or "allowed", clock or None, n.access_code, "")
        facts.requires_ack, facts.ack_url = n.requires_ack, ack_url(settings.public_base_url, n.id)
        if n.function_code:
            fn = db.scalar(select(models.CriticalFunction).where(
                models.CriticalFunction.facility_id == facility.id, models.CriticalFunction.code == n.function_code))
            facts.function_name = fn.name.lower() if fn else n.function_code.replace("_", " ")
            times = msg._TIME.findall(n.duty_instruction)
            if times:
                facts.report_time = ":".join(times[-1])
        decorate_facts(db, facility, rec, facts)
    else:
        rec = latest_recommendation(db, facility.id)
        if rec:
            decorate_facts(db, facility, rec, facts)
        elif settings.demo_mode:
            facts.footer = "Demo: synthetic office, no approved alert notice"
        facts.requires_ack = False
    lines = msg.template(facts)[1].splitlines()
    if not n and not current_notice(db, facility.id, sent_only=True):
        lines[0] = ("Sin decisión aprobada. " if facts.lang == "es" else "No approved notice yet. ") + lines[0]
    return lines

@router.get("/notices/{notice_id}", response_model=schemas.NoticeDetail, dependencies=[Depends(manager_only)])
def get_notice(notice_id: str, db: Session = Depends(get_db)):
    return notice_detail(db, _get_notice(db, notice_id))


@router.post("/notices/{notice_id}/call-ins", response_model=schemas.Notification,
             dependencies=[Depends(manager_only)])
def post_call_in(notice_id: str, body: schemas.CallInIn, db: Session = Depends(get_db)):
    try:
        return call_in(db, notice_id, body)
    except NoDecisionError as e:
        raise HTTPException(404, str(e))
    except CallInError as e:
        raise HTTPException(409, str(e))


# ---------------------------------------------------------------- security desk: access list

@router.get("/notices/{notice_id}/access-list", response_model=schemas.AccessList,
            dependencies=[Depends(require("facility_manager", "security_desk"))])
def notice_access_list(notice_id: str, db: Session = Depends(get_db), v: Viewer = Depends(viewer)):
    notice = _get_notice(db, notice_id)
    facility = db.get(models.Facility, notice.facility_id)
    if v.role == "security_desk" and notice.operating_date != utcnow().astimezone(ZoneInfo(facility.timezone)).date():
        raise HTTPException(403, "Security desk sees today's access list only")
    return _access_list(db.get(models.Facility, notice.facility_id), plan_for_notice(db, notice), notice)


@router.get("/facilities/{facility_id}/access-list", response_model=schemas.AccessList,
            dependencies=[Depends(require("facility_manager", "security_desk"))])
def current_access_list(facility_id: str, db: Session = Depends(get_db)):
    """Today's list: from the latest notice, or (before any decision) normal operations."""
    facility = db.get(models.Facility, facility_id)
    if facility is None:
        raise HTTPException(404, "facility not found")
    today = utcnow().astimezone(ZoneInfo(facility.timezone)).date()
    notice = db.scalar(select(models.Notice).where(models.Notice.facility_id == facility_id,
        models.Notice.archived_at.is_(None), models.Notice.operating_date == today).order_by(models.Notice.created_at.desc()).limit(1))
    if notice:
        return _access_list(facility, plan_for_notice(db, notice), notice)
    return _access_list(facility, normal_plan(db, facility), None)


# ---------------------------------------------------------------- supervisor: team board

@router.get("/team", response_model=schemas.TeamBoard)
def team(supervisor_id: str | None = None, v: Viewer = Depends(require("supervisor", "facility_manager")),
         db: Session = Depends(get_db)):
    sup_id = v.person_id if v.role == "supervisor" else supervisor_id
    sup = db.get(models.Employee, sup_id) if sup_id else None
    if sup is None:
        raise HTTPException(400, "supervisor_id is required")
    members = db.scalars(select(models.Employee).where(models.Employee.supervisor_id == sup.id)
                         .order_by(models.Employee.name)).all()
    ids = {m.id for m in members}
    facility = facility_of(db, sup.id)
    notice = current_notice(db, facility.id)
    rows = (db.scalars(select(models.Notification).where(models.Notification.notice_id == notice.id)).all()
            if notice else _transient(live_plan(db, facility)))
    names = {m.id: m.name for m in members}
    mine = [n for n in rows if n.employee_id in ids and n.message_type not in ("supervisor_summary", "access_list",
                                                                                 "staffing_alert")]
    out = [schemas.TeamNotification(
        notification_id=n.id, employee_id=n.employee_id, name=names[n.employee_id], message_type=n.message_type,
        included=n.included, audience_text=n.audience_text, duty_instruction=n.duty_instruction,
        building_access=n.building_access, wave=n.wave, notify_by=n.notify_by, channels_planned=n.channels_planned,
        delivered=any(r.get("ok") for r in (n.channel_results or {}).values()) if n.sent_at else None,
        sent_at=n.sent_at, requires_ack=n.requires_ack, acknowledged_at=n.acknowledged_at,
        escalation_level=n.escalation_level) for n in mine]
    designation = {m.id: m.designation for m in members}
    suggestions = [schemas.ExposureSuggestion(
        employee_id=n.employee_id, name=names[n.employee_id], audience_text=n.audience_text,
        suggestion=("Accommodation on file: offer unscheduled telework per agency policy"
                    if "SUPERVISOR_SUGGESTION" in n.reason_codes else "consider offering unscheduled telework/leave per agency policy"))
        for n in mine if (EXPOSURE_CODES | {"SUPERVISOR_SUGGESTION"}) & set(n.reason_codes)
        and designation[n.employee_id] != "emergency"]
    return schemas.TeamBoard(
        supervisor=schemas.EmployeePublic.model_validate(sup),
        notice=schemas.Notice.model_validate(notice) if notice else None,
        members=[schemas.EmployeePublic.model_validate(m) for m in members],
        notifications=sorted(out, key=lambda t: (t.wave, t.name)), suggestions=suggestions)


# ---------------------------------------------------------------- employee: my status and acks

@router.get("/me", response_model=schemas.MyStatus)
def my_status(v: Viewer = Depends(require("employee")), db: Session = Depends(get_db)):
    person = db.get(models.Employee, v.person_id)
    facility = facility_of(db, person.id)
    notice = current_notice(db, facility.id, sent_only=True)
    n = None
    if notice:
        n = db.scalar(select(models.Notification).join(models.Notice, models.Notification.notice_id == models.Notice.id)
                      .where(models.Notice.facility_id == facility.id,
                             models.Notice.archived_at.is_(None), models.Notification.archived_at.is_(None),
                             models.Notice.operating_date == notice.operating_date,
                             models.Notification.employee_id == person.id, models.Notification.sent_at.is_not(None),
                             models.Notification.message_type.in_(["action_required", "fyi", "call_in"]))
                      .order_by(models.Notification.sent_at.desc()).limit(1))
    if n is not None:
        own = schemas.Notification.model_validate(n).model_copy(update={"reason_text": n.audience_text})
        return schemas.MyStatus(
            employee=schemas.EmployeePublic.model_validate(person), facility_name=facility.name,
            operating_date=notice.operating_date, posture=notice.posture, notification=own,
            duty_instruction=n.duty_instruction, building_access=n.building_access,
            ack_url=ack_url(settings.public_base_url, n.id) if n.requires_ack else None,
            message_lines=status_lines(db, facility, plan_for_notice(db, db.get(models.Notice, n.notice_id)), person, n),
            facility_timezone=facility.timezone)
    plan = live_plan(db, facility) if not notice else plan_for_notice(db, notice)
    imp = next((i for i in plan.impacts if i.employee.id == person.id), None)
    return schemas.MyStatus(
        employee=schemas.EmployeePublic.model_validate(person), facility_name=facility.name,
        operating_date=plan.ctx.operating_date, posture=notice.posture if notice else None,
        duty_instruction=imp.duty_instruction if imp else "no change",
        building_access=imp.access.label if imp and imp.access else "allowed",
        message_lines=status_lines(db, facility, plan, person), facility_timezone=facility.timezone)


@router.post("/notifications/{notification_id}/ack", response_model=schemas.Notification)
def ack(notification_id: str, v: Viewer = Depends(require("employee", "facility_manager")), db: Session = Depends(get_db)):
    n = db.get(models.Notification, notification_id)
    if n is None:
        raise HTTPException(404, "notification not found")
    if v.role == "employee" and n.employee_id != v.person_id:
        raise HTTPException(403, "you can only acknowledge your own notifications")
    if v.role not in ("employee", "facility_manager"):
        raise HTTPException(403, "not available to this role")
    return acknowledge(db, notification_id)


# ---------------------------------------------------------------- role switcher and demo personas

@router.get("/viewers", response_model=schemas.Viewers)
def viewers(db: Session = Depends(get_db)):
    people = db.scalars(select(models.Employee).order_by(models.Employee.name)).all()
    pub = [schemas.EmployeePublic.model_validate(p) for p in people]
    by_id = {p.id: p for p in people}
    personas = []
    for key, desc in PERSONAS.items():
        p = by_id.get(emp_id(key))
        if p:
            personas.append(schemas.Persona(employee_id=p.id, name=p.name, role=p.role, ntfy_topic=p.ntfy_topic,
                                            description=desc))
    return schemas.Viewers(
        supervisors=[p for p in pub if p.role in ("supervisor", "facility_manager")],
        security_desk=[p for p in pub if p.role == "security_desk"],
        employees=pub, personas=personas)


class SimulateIn(BaseModel):
    kind: str  # "zone_alert" | "road_closure" | "clear"
    zone: str | None = None  # e.g. MOZ043 (Cass County, MO forecast zone)
    route: str | None = None  # e.g. I-49
    event: str = "Winter Storm Warning"
    hours: int = 12


@router.post("/demo/simulate", dependencies=[Depends(manager_only)])
def simulate(body: SimulateIn, facility_id: str = "usda-kc-demo", db: Session = Depends(get_db)):
    """DEMO_MODE only: inject a home-zone warning or a road closure (clearly marked as simulated)
    so the exposure flow can be shown on a calm day."""
    if not settings.demo_mode:
        raise HTTPException(403, "simulation requires DEMO_MODE=1")
    now = utcnow()
    if body.kind == "clear":
        for e in db.scalars(select(models.Evidence).where(models.Evidence.source == "demo",
                                                          models.Evidence.facility_id == facility_id)):
            e.valid_until = now
        record.append(db, "demo.simulation_cleared", {"facility_id": facility_id}, facility_id=facility_id)
        db.commit()
        return {"cleared": True}
    if body.kind == "zone_alert" and body.zone:
        kind, value = "simulated_zone_alert", {"zone": body.zone, "event": body.event, "onset": now.isoformat(),
                                               "ends": (now + timedelta(hours=body.hours)).isoformat()}
    elif body.kind == "road_closure" and body.route:
        kind, value = "road_closure", {"route": body.route, "description": f"SIMULATED closure of {body.route}"}
    else:
        raise HTTPException(422, "zone_alert needs zone; road_closure needs route")
    ev = models.Evidence(id=uuid.uuid4().hex, facility_id=facility_id, source="demo", kind=kind,
                         value={**value, "simulated": True}, retrieved_at=now, valid_until=now + timedelta(hours=body.hours),
                         relevance_note="SIMULATED for the demo (DEMO_MODE); not real NWS or MoDOT data.", status="ok")
    db.add(ev)
    record.append(db, "demo.simulated", {"evidence_id": ev.id, "kind": kind, **value}, facility_id=facility_id)
    db.commit()
    return {"evidence_id": ev.id, "kind": kind, **value}


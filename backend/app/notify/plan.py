"""Notice plan: turns impacts into planned notifications (who gets what, when, how), role messages
(supervisor summaries, access list, staffing alert), and the diff against the previous notice.

build_plan() is pure. load_inputs() / context_for() read the database.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..access.policy import Access, access_for
from ..staffing.coverage import CallIn, coverage
from . import instructions as ins
from . import messages as msg
from .delivery import channels_for, decision_clock, wave_for
from .impact import Exposure, Impact, PostureContext, compute_impacts, fmt, public_codes

PERSONAL = ("action_required", "fyi")
WAVE_ORDER = {"1": 0, "2": 1, "fyi": 2}

# Names for the other demo offices people are detailed to/from (not modeled as facilities).
OTHER_OFFICES = {
    "usda-stl-demo": "USDA St. Louis Office (demo)",
    "usda-omaha-demo": "USDA Omaha Office (demo)",
    "usda-wichita-demo": "USDA Wichita Office (demo)",
}


@dataclass
class Previous:
    """What a person was last sent for this facility and date (from the superseded notice)."""
    message_type: str
    signature: str
    duty_instruction: str
    sent_at: datetime | None


@dataclass
class Planned:
    id: str
    employee: schemas.Employee
    message_type: str
    included: bool
    reason_codes: list[str]
    reason_text: str
    audience_text: str
    duty_instruction: str = ""
    access: Access | None = None
    notify_by: datetime | None = None
    wave: str = "fyi"
    requires_ack: bool = False
    channels: list[str] = field(default_factory=list)
    change: str = "initial"
    function_code: str | None = None
    facts: msg.MessageFacts | None = None  # personal messages: drafted or templated later
    subject: str | None = None
    message: str | None = None  # role messages: final text
    drafted_by: str | None = None
    group_key: str | None = None
    signature: str = ""
    render: frozenset[str] = frozenset()

    @property
    def family(self) -> str:
        return "personal" if self.message_type in PERSONAL else self.message_type


@dataclass
class Plan:
    ctx: PostureContext
    impacts: list[Impact]
    planned: list[Planned]
    coverage: list[schemas.CoverageItem]
    clock: schemas.DecisionClock
    access_rows: list[schemas.AccessRow]

    @property
    def exposed_count(self) -> int:
        return sum(1 for i in self.impacts if i.exposed)

    def summary(self) -> schemas.PeopleSummary:
        counts: dict[str, int] = {}
        for i in self.impacts:
            for c in i.public_codes:
                counts[c] = counts.get(c, 0) + 1
        return schemas.PeopleSummary(
            posture=self.ctx.posture, params=self.ctx.params(), clock=self.clock,
            exposed_count=self.exposed_count, coverage=self.coverage, counts_by_code=counts,
            action_required=sum(1 for i in self.impacts if i.message_type == "action_required"),
            fyi=sum(1 for i in self.impacts if i.message_type == "fyi"))


def signature(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, ensure_ascii=False).encode()).hexdigest()[:32]


def ack_url(base_url: str, notification_id: str) -> str:
    return f"{base_url}/ack/{notification_id}"


def _window(access: Access, ctx: PostureContext, imp: Impact) -> str:
    shift = imp.shift
    span = f"{fmt(shift.start)}-{fmt(shift.end)}" if shift else "not scheduled"
    if access.status == "allowed_after":
        return f"from {access.time}"
    if access.status == "allowed_until":
        return f"until {access.time}"
    if access.status == "denied":
        return "none"
    return f"shift {span}" if shift else "normal hours"


def access_rows(ctx: PostureContext, impacts: list[Impact]) -> list[schemas.AccessRow]:
    rows = []
    for i in impacts:
        if i.access is None or "DETAIL_OUT" in i.codes:
            continue
        p = i.employee
        rows.append(schemas.AccessRow(employee_id=p.id, name=p.name, role=p.role, access_level=p.access_level,
                                      access=i.access.label, window=_window(i.access, ctx, i), code=i.access.code,
                                      reason=i.access.reason, scheduled=i.scheduled))
    order = {"allowed": 0, "allowed_with_escort": 1, "allowed_after": 2, "allowed_until": 3, "denied": 4}
    return sorted(rows, key=lambda r: (order.get(r.access.split()[0], 9), r.name))


def _group_key(p: Planned, ctx: PostureContext, instruction_key: str) -> str:
    f = p.facts
    return signature("v1", p.message_type, p.change, ctx.posture, ctx.params(), instruction_key, f.lang,
                     "plain_language" in f.render, p.requires_ack, bool(f.update_line))


def _update_line(lang: str, new: str, old: str, sent_at: datetime | None, ctx: PostureContext) -> tuple[str, list[str]]:
    when = fmt(sent_at.astimezone(ctx.tz)) if sent_at else None
    if lang == "es":
        line = f"Actualización: ahora {new} (antes: {old})" + (f"; reemplaza el aviso de las {when}." if when else ".")
    else:
        line = f"Update: now {new} (before: {old})" + (f", replacing the {when} notice." if when else ".")
    return line, [when] if when else []


def build_plan(ctx: PostureContext, people: list[schemas.Employee], assignments: list[schemas.Assignment],
               shifts: list[schemas.Shift], functions: list[schemas.CriticalFunction], exposure: Exposure, *,
               base_url: str, previous: dict[tuple[str, str], Previous] | None = None,
               call_ins: list[CallIn] = (), facility_names: dict[str, str] | None = None) -> Plan:
    previous = previous or {}
    names = {**OTHER_OFFICES, **(facility_names or {})}
    impacts = compute_impacts(ctx, people, assignments, shifts, exposure, names)
    cov = coverage(functions, impacts, ctx.posture, list(call_ins))
    clock = decision_clock(impacts)
    rows = access_rows(ctx, impacts)
    local_now = ctx.now.astimezone(ctx.tz)
    planned: list[Planned] = []

    # ---- personal messages
    for imp in impacts:
        p = imp.employee
        pid = uuid.uuid4().hex
        item = Planned(
            id=pid, employee=p, message_type=imp.message_type, included=imp.message_type in PERSONAL,
            reason_codes=imp.public_codes, reason_text=imp.reason_text, audience_text=imp.audience_text,
            duty_instruction=imp.duty_instruction, access=imp.access, notify_by=imp.notify_by,
            requires_ack=imp.requires_ack, render=imp.render)
        item.signature = signature(item.message_type, item.duty_instruction, imp.access.label if imp.access else "")
        prev = previous.get((p.id, "personal"))
        update_line, extra_times = None, []
        if prev and prev.sent_at:
            if item.message_type == "none" and prev.message_type == "action_required":
                item.message_type, item.included, item.change = "fyi", True, "all_clear"
                item.duty_instruction = ins.text("all_clear", p.language)
                item.reason_codes = item.reason_codes + ["ALL_CLEAR"]
                item.reason_text += "; earlier notice no longer applies (all clear)"
            elif item.included and prev.signature == item.signature:
                item.included, item.change = False, "unchanged"
                item.reason_text += "; unchanged since the previous notice (not resent)"
            elif item.included:
                item.change = "update"
                update_line, extra_times = _update_line(p.language, item.duty_instruction, prev.duty_instruction,
                                                        prev.sent_at, ctx)
        elif item.message_type == "none":
            item.change = "initial"
        if item.included:
            item.wave = wave_for(item.message_type, wave1=imp.wave1, notify_by=item.notify_by, now=ctx.now)
            item.channels = channels_for(p, item.message_type, local_now)
            if "TELEWORK_SCHEDULED" in imp.codes:
                item.channels = ["email"]  # never push
            item.facts = msg.MessageFacts(
                name=p.name, language=p.language, facility_name=ctx.facility_name,
                operating_date=ctx.operating_date, posture=ctx.posture,
                delay_hours=int(ctx.delay.total_seconds() // 3600), message_type=item.message_type,
                change=item.change, instruction=item.duty_instruction, access=imp.access, why_codes=imp.codes,
                requires_ack=item.requires_ack, ack_url=ack_url(base_url, pid), update_line=update_line,
                render=imp.render, extra_times=extra_times)
            item.group_key = _group_key(item, ctx, imp.instruction_key or item.change)
        planned.append(item)

    # ---- role messages
    for imp in impacts:
        p = imp.employee
        if "DETAIL_OUT" in imp.codes:
            continue
        if p.role in ("supervisor", "facility_manager"):
            team = [i for i in impacts if (i.employee.supervisor_id == p.id or p.role == "facility_manager")
                    and i.employee.id != p.id and "DETAIL_OUT" not in i.codes]
            suggestions = [{"employee_id": i.employee.id, "name": i.employee.name, "audience_text": i.audience_text,
                            "suggestion": i.supervisor_suggestion} for i in team if i.supervisor_suggestion]
            if team and (ctx.posture != "normal" or suggestions):
                rows_ = [{"name": i.employee.name, "message_type": i.message_type, "codes": i.public_codes}
                         for i in team]
                subject, body = msg.supervisor_summary(p.name, ctx.facility_name, ctx.operating_date, ctx.posture,
                                                       rows_, suggestions, f"{base_url}/team")
                due = [i.notify_by for i in team if i.notify_by] + [
                    i.shift.start - timedelta(minutes=i.employee.commute_minutes + 30)
                    for i in team if i.supervisor_suggestion and i.shift]
                planned.append(_role(p, "supervisor_summary", subject, body, ctx, local_now,
                                     ["SUPERVISOR_SUMMARY"] + (["EXPOSURE_SUGGESTION"] if suggestions else []),
                                     f"team summary ({len(team)} people, {len(suggestions)} exposure suggestion(s))",
                                     min(due) if due else None, wave1=bool(suggestions)))
        if p.role == "security_desk" and ctx.posture != "normal":
            subject, body = msg.access_list_message(ctx.facility_name, ctx.operating_date, ctx.posture,
                                                    [r.model_dump() for r in rows], f"{base_url}/security")
            planned.append(_role(p, "access_list", subject, body, ctx, local_now, ["SECURITY_ACCESS_LIST"],
                                 f"building access list ({len(rows)} people)", None, wave1=True))
        if p.role == "facility_manager":
            short = [c for c in cov if c.status != "covered"]
            if short:
                subject, body = msg.staffing_alert_message(ctx.facility_name, ctx.operating_date,
                                                           [c.model_dump() for c in short], f"{base_url}/decide")
                planned.append(_role(p, "staffing_alert", subject, body, ctx, local_now, ["STAFFING_SHORTFALL"],
                                     "; ".join(c.label for c in short), None, wave1=True))

    # Role messages: don't resend identical content.
    for item in planned:
        if item.family == "personal":
            continue
        prev = previous.get((item.employee.id, item.family))
        if prev and prev.sent_at and prev.signature == item.signature:
            item.included, item.change = False, "unchanged"
        elif prev and prev.sent_at:
            item.change = "update"

    planned.sort(key=lambda x: (not x.included, WAVE_ORDER.get(x.wave, 3),
                                x.notify_by or datetime.max.replace(tzinfo=ctx.tz), x.employee.name))
    return Plan(ctx=ctx, impacts=impacts, planned=planned, coverage=cov, clock=clock, access_rows=rows)


def _role(person: schemas.Employee, message_type: str, subject: str, body: str, ctx: PostureContext,
          local_now: datetime, codes: list[str], reason: str, notify_by: datetime | None, *, wave1: bool) -> Planned:
    p = Planned(id=uuid.uuid4().hex, employee=person, message_type=message_type, included=True,
                reason_codes=codes, reason_text=reason, audience_text=reason, notify_by=notify_by,
                subject=subject, message=body, drafted_by="template",
                wave="1" if wave1 else wave_for(message_type, wave1=False, notify_by=notify_by, now=ctx.now),
                channels=channels_for(person, message_type, local_now))
    if message_type == "supervisor_summary" and wave1:
        p.wave = "1"
    elif message_type == "supervisor_summary":
        p.wave = "2"
    # URLs and "first name:" don't change meaning; everything else does.
    p.signature = signature(message_type, body.split("\n", 1)[-1].rsplit("\n", 1)[0])
    return p


# ---------------------------------------------------------------- database inputs

def load_inputs(db: Session, facility: models.Facility, day: date):
    """People linked to the facility (all assignments), their shifts for day-1..day, functions."""
    assignments = [schemas.Assignment.model_validate(a) for a in db.scalars(select(models.Assignment))]
    ids = {a.employee_id for a in assignments if a.facility_id == facility.id}
    people = [schemas.Employee.model_validate(e) for e in
              db.scalars(select(models.Employee).where(models.Employee.id.in_(ids)).order_by(models.Employee.name))]
    shifts = [schemas.Shift.model_validate(s) for s in db.scalars(select(models.Shift).where(
        models.Shift.employee_id.in_(ids), models.Shift.date.in_([day - timedelta(days=1), day])))]
    functions = [schemas.CriticalFunction.model_validate(f) for f in db.scalars(
        select(models.CriticalFunction).where(models.CriticalFunction.facility_id == facility.id))]
    names = {f.id: f.name for f in db.scalars(select(models.Facility))}
    return people, [a for a in assignments if a.employee_id in ids], shifts, functions, names


def context_for(facility: models.Facility, operating_date: date, posture: str, now: datetime,
                functions: list[schemas.CriticalFunction], delay_hours: int | None = None,
                dismissal_time: str | None = None) -> PostureContext:
    return PostureContext(
        facility_id=facility.id, facility_name=facility.name, timezone=facility.timezone,
        operating_date=operating_date, posture=posture, open_time=facility.open_time,
        close_time=facility.close_time, now=now, delay_hours=delay_hours, dismissal_time=dismissal_time,
        critical_codes=frozenset(f.code for f in functions))


def access_for_person(person: schemas.Employee, ctx: PostureContext) -> Access:
    return access_for(person.access_level, ctx.posture, report_time=fmt(ctx.facility_report_dt),
                      dismissal_time=fmt(ctx.dismissal_dt), functions=person.functions,
                      critical_codes=ctx.critical_codes)

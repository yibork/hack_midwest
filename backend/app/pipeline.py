"""One evaluation: weather agent -> rules engine (pure, unchanged) -> writer agent -> stored
Recommendation, all under one evaluation_id.

Then: employee home-zone alerts are stored as Evidence (people exposure), and message wording is
pre-drafted in the background so approval can send at once.

If an agent is unreachable, errors, or takes longer than AGENT_TIMEOUT_SECONDS, the same tools
are called directly in code, the template rationale is used, and "agent_unavailable" is recorded.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calendar, models, record, scheduler, schemas
from .agents import message_agent, weather_agent, writer_agent
from .agents.local import run as local_run
from .agents.runtime import AgentUnavailable, new_state, record_unavailable
from .config import settings
from .db import utcnow
from .notify import exposure
from .notify.plan import load_inputs
from .rules.engine import evaluate
from .services import store_evidence_now
from .sources import local_signals, nws


def choose_target(fac: schemas.Facility, now: datetime, target_date: date | None = None,
                  demo_workday: bool = False, scheduled: bool = False) -> tuple[date, str, str]:
    """-> (operating date, mode, why). Default: the next operating day (facility days + federal
    holidays), starting today until close time, then tomorrow."""
    tz = ZoneInfo(fac.timezone)
    local = now.astimezone(tz)
    if demo_workday:
        if not settings.demo_mode:
            raise ValueError("demo_workday requires DEMO_MODE=1")
        why = calendar.why_not(local.date(), fac.operating_days)
        return local.date(), "demo_workday", (f"DEMO: today ({calendar.label(local.date())}) evaluated as a workday"
                                              + (f"; really, {why[0].lower() + why[1:]}." if why else "."))
    if target_date is not None and scheduled:
        return target_date, "scheduled", f"Scheduled re-evaluation for {calendar.label(target_date)}: the deterministic check changed."
    if target_date is not None:
        why = calendar.why_not(target_date, fac.operating_days)
        return target_date, "explicit", (f"{why}: only people scheduled that day (weekend crew, emergency staff) are "
                                         f"affected." if why else f"Picked by the manager: {calendar.label(target_date)}.")
    start = local.date()
    if local.time() >= datetime.strptime(fac.close_time, "%H:%M").time():
        start += timedelta(days=1)
    day, why = calendar.next_operating_day(start, fac.operating_days)
    return day, "next_operating_day", why


def evaluate_facility(db: Session, facility: models.Facility, evaluation_id: str | None = None,
                      target_date: date | None = None, demo_workday: bool = False,
                      scheduled: bool = False) -> models.Recommendation:
    eid = evaluation_id or uuid.uuid4().hex
    fac = schemas.Facility.model_validate(facility)
    day, mode, why = choose_target(fac, utcnow(), target_date, demo_workday, scheduled)
    record.append_now("evaluation.started", {"evaluation_id": eid, "facility": fac.name,
                                             "operating_date": day.isoformat(), "target_mode": mode, "why": why},
                      facility_id=fac.id, evaluation_id=eid)

    # People context (no network): who works here, their home counties and commute routes.
    people = load_inputs(db, facility, day)[0] if not fac.temporary else []

    # 1c (started first). Local signals run in parallel with the weather agent: coordinator +
    #     roads/schools/public-safety/news agents over allowlisted sources. They store validated
    #     facts as Evidence; rules decide what those can change.
    local_job = None
    if people and settings.local_signals:
        local_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-signals")
        local_job = local_pool.submit(_local_signals, fac, eid, people)
        local_pool.shutdown(wait=False)

    # 1. Evidence: the weather agent calls the NWS tools and annotates what they stored.
    state = new_state(evaluation_id=eid, facility_id=fac.id, operating_date=day)
    agent_ran = False
    try:
        notes = weather_agent.run(fac, state, settings.agent_timeout)
        agent_ran = True
        weather_agent.apply_notes(notes, fac.id, eid)
    except AgentUnavailable as e:
        record_unavailable(weather_agent.NAME, str(e), "NWS tools called directly", state)
    weather_agent.ensure_required_evidence(fac, eid, agent_ran, operating_date=day)

    # 1b. People exposure: alerts for employees' home zones, one request per unique zone.
    if people:
        exposure.collect(fac.id, eid, people)
    if local_job is not None:
        try:
            local_job.result(timeout=settings.local_agent_timeout * 2 + 30)
        except Exception as e:  # noqa: BLE001 - local signals are optional; the engine notes what's missing
            record.append_now("local.failed", {"evaluation_id": eid, "error": f"{type(e).__name__}: {e}"[:200]},
                              facility_id=fac.id, evaluation_id=eid)
    evidence = weather_agent.evidence_for(eid)

    # 2. Recommendation: deterministic rules engine over the stored Evidence rows.
    now = utcnow()
    rec = evaluate(fac, evidence, now, operating_date=day, target_note=why, target_mode=mode)
    rec.evaluation_id = eid

    # 3. Explanation: writer agent, validated; template on any problem.
    rec.rationale, rec.rationale_source, rec.rationale_note = writer_agent.write_rationale(
        rec, evidence, facility_name=fac.name, tz=fac.timezone,
        state=new_state(evaluation_id=eid, facility_id=fac.id), timeout=settings.agent_timeout)

    row = models.Recommendation(**rec.model_dump(mode="python", exclude={"triggers"}),
                                triggers=[t.model_dump() for t in rec.triggers])
    db.add(row)
    record.append(db, "recommendation.created", {
        "recommendation_id": rec.id, "evaluation_id": eid, "posture": rec.posture,
        "confidence": rec.confidence, "operating_date": rec.operating_date,
        "policy_version": rec.policy_version, "triggers": [t.rule_id for t in rec.triggers],
        "target_mode": rec.target_mode, "target_note": rec.target_note, "lead_time_hours": rec.lead_time_hours,
        "rationale_note": rec.rationale_note,
        "evidence_ids": rec.evidence_ids, "rationale_source": rec.rationale_source,
    }, facility_id=fac.id, evaluation_id=eid)
    db.commit()
    if not fac.temporary:
        scheduler.schedule_for(db, facility, day)  # 15:00 the day before, 04:00 on the day
        message_agent.start_predraft(row.id)  # wording ready before the manager approves
    return row


def recheck(db: Session, facility: models.Facility, day: date, job_key: str | None = None) -> dict:
    """Scheduled re-evaluation: deterministic first (no model calls). Runs the full pipeline with
    agents only if posture, triggers or confidence changed since the latest recommendation."""
    eid = uuid.uuid4().hex
    fac = schemas.Facility.model_validate(facility)
    record.append_now("scheduled.recheck_started", {"evaluation_id": eid, "job": job_key,
                                                    "operating_date": day.isoformat()},
                      facility_id=fac.id, evaluation_id=eid)
    weather_agent.ensure_required_evidence(fac, eid, agent_ran=False, operating_date=day)
    people = load_inputs(db, facility, day)[0]
    if people:
        exposure.collect(fac.id, eid, people)
        if settings.local_signals:
            _local_signals(fac, eid, people, use_models=False)
    now = utcnow()
    det = evaluate(fac, weather_agent.evidence_for(eid), now, operating_date=day, target_mode="scheduled")
    prev = db.scalar(select(models.Recommendation).where(models.Recommendation.facility_id == fac.id,
                                                         models.Recommendation.operating_date == day)
                     .order_by(models.Recommendation.evaluated_at.desc()).limit(1))
    prev_triggers = sorted(t["rule_id"] for t in prev.triggers) if prev else None
    changes = [] if prev else ["no earlier recommendation for this day"]
    if prev and prev.posture != det.posture:
        changes.append(f"posture {prev.posture} -> {det.posture}")
    if prev and prev_triggers != sorted(t.rule_id for t in det.triggers):
        changes.append(f"triggers {prev_triggers} -> {sorted(t.rule_id for t in det.triggers)}")
    if prev and prev.confidence != det.confidence:
        changes.append(f"confidence {prev.confidence} -> {det.confidence}")
    result = {"evaluation_id": eid, "changed": bool(changes), "changes": changes, "posture": det.posture,
              "confidence": det.confidence, "lead_time_hours": det.lead_time_hours}
    if changes:
        full = evaluate_facility(db, facility, target_date=day, scheduled=True)
        result["recommendation_id"] = full.id
    record.append_now("scheduled.recheck", {**result, "job": job_key}, facility_id=fac.id, evaluation_id=eid)
    return result


def _local_signals(fac: schemas.Facility, eid: str, people: list[schemas.Employee], use_models: bool = True) -> None:
    """Facility county + employee home counties (corroboration rules), then the local agents."""
    point, _ = nws.get_point(fac.lat, fac.lon)  # cached
    county = local_signals.COUNTY_NAMES.get((point or {}).get("county") or "")
    homes: dict[str, int] = {}
    for p in people:
        name = local_signals.COUNTY_NAMES.get(p.home_county or "")
        if name:
            homes[name] = homes.get(name, 0) + 1
    store_evidence_now([schemas.Evidence(
        id=uuid.uuid4().hex, source="roster", kind="people_area",
        value={"facility_county": county, "home_counties": homes}, retrieved_at=utcnow(), status="ok",
        relevance_note="Facility county and employee home counties (for local-signal corroboration).")],
        fac.id, eid)
    alerts, _ = nws.get_active_alerts_for_point(fac.lat, fac.lon)  # cached; the weather agent reads the same
    local_run.run(fac, eid, alerts or [], people, county, use_models=use_models)


def evaluate_point(db: Session, lat: float, lon: float, name: str,
                   evaluation_id: str | None = None) -> models.Recommendation:
    """Evaluate an arbitrary location as a temporary facility (default 07:30-16:00 hours)."""
    point, _ = nws.get_point(lat, lon)  # cached; only used for the time zone here
    facility = models.Facility(
        id=f"tmp-{uuid.uuid4().hex[:10]}", name=name, address=f"{lat:.4f}, {lon:.4f}",
        lat=lat, lon=lon, timezone=(point or {}).get("timeZone") or "America/Chicago",
        open_time="07:30", close_time="16:00", access_routes=[], temporary=True,
    )
    db.add(facility)
    db.commit()  # agent tools read it from their own sessions
    return evaluate_facility(db, facility, evaluation_id)

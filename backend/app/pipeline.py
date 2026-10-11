"""One evaluation: weather agent -> rules engine (pure, unchanged) -> writer agent -> stored
Recommendation, all under one evaluation_id.

Then: employee home-zone alerts are stored as Evidence (people exposure), and message wording is
pre-drafted in the background so approval can send at once.

If an agent is unreachable, errors, or takes longer than AGENT_TIMEOUT_SECONDS, the same tools
are called directly in code, the template rationale is used, and "agent_unavailable" is recorded.
"""
import threading
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calendar, models, record, scheduler, schemas
from .agents import message_agent, weather_agent, writer_agent
from .agents.local import run as local_run
from .agents.runtime import AgentUnavailable, new_state, record_unavailable
from .config import settings
from .db import SessionLocal, utcnow
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
        return local.date(), "demo_workday", calendar.demo_workday_note(local.date())
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
    if facility.demo_origin_id:
        from .demo_location import evaluate_live
        return evaluate_live(db, facility)
    eid = evaluation_id or uuid.uuid4().hex
    fac = schemas.Facility.model_validate(facility)
    day, mode, why = choose_target(fac, utcnow(), target_date, demo_workday, scheduled)
    record.append_now("evaluation.started", {"evaluation_id": eid, "facility": fac.name,
                                             "operating_date": day.isoformat(), "target_mode": mode, "why": why},
                      facility_id=fac.id, evaluation_id=eid)

    # People context (no network): who works here, their home counties and commute routes.
    people = load_inputs(db, facility, day)[0] if not fac.temporary else []

    # 1c (started first). Local signals run in parallel with the weather agent. Phase A (fetch,
    #     pre-filter, roads cache lookup) is fast; phase B runs agents live only on a cache miss.
    local = _LocalJob(fac, eid, people, day) if people and settings.local_signals else None

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
    from .sources import airnow
    store_evidence_now(airnow.collect(fac, day), fac.id, eid)

    # 1b. People exposure: alerts for employees' home zones, one request per unique zone.
    if people:
        exposure.collect(fac.id, eid, people)
    pending = local.wait_phase_a() if local else []

    # 2. Recommendation: deterministic rules engine over the stored Evidence rows. If a local agent
    #    (the roads agent on a cache miss) is still running live, the recommendation is preliminary:
    #    shown at once, decisions blocked, finalized when its facts arrive.
    now = utcnow()
    rec = evaluate(fac, weather_agent.evidence_for(eid), now, operating_date=day, target_note=why, target_mode=mode)
    rec.evaluation_id = eid
    rec.rationale_source, rec.template_rationale = "template", rec.rationale
    rec.status = "preliminary" if pending else "final"
    if pending:
        rec.rationale_note = PRELIMINARY_NOTE if "roads" in pending else "Preliminary - local checks in progress"
    row = models.Recommendation(**rec.model_dump(mode="python", exclude={"triggers"}),
                                triggers=[t.model_dump() for t in rec.triggers])
    db.add(row)
    record.append(db, "recommendation.created", {
        "recommendation_id": rec.id, "evaluation_id": eid, "posture": rec.posture, "status": rec.status,
        "pending_agents": pending, "confidence": rec.confidence, "operating_date": rec.operating_date,
        "policy_version": rec.policy_version, "triggers": [t.rule_id for t in rec.triggers],
        "target_mode": rec.target_mode, "target_note": rec.target_note, "lead_time_hours": rec.lead_time_hours,
        "rationale_note": rec.rationale_note,
        "evidence_ids": rec.evidence_ids, "rationale_source": rec.rationale_source,
    }, facility_id=fac.id, evaluation_id=eid)
    db.commit()
    if pending:
        threading.Thread(target=_finalize, args=(row.id, fac, eid, day, why, mode, local), daemon=True,
                         name=f"finalize-{row.id[:8]}").start()
    else:
        _after_final(db, row, fac, eid)
    return row


PRELIMINARY_NOTE = "Preliminary - road check in progress"
_finalizers: dict[str, threading.Event] = {}


class _LocalJob:
    """Runs the local-signals pass in a thread; phase_a is set once fetch/cache lookup is done."""

    def __init__(self, fac: schemas.Facility, eid: str, people: list[schemas.Employee], day: date):
        self.phase_a, self.done = threading.Event(), threading.Event()
        self.lp, self.error = None, None
        self.fac, self.eid = fac, eid
        threading.Thread(target=self._run, args=(fac, eid, people, day), daemon=True,
                         name=f"local-{eid[:8]}").start()

    def _run(self, fac, eid, people, day):
        try:
            alerts, county = _local_prepare(fac, eid, people)
            self.lp = local_run.start(fac, eid, alerts, people, county, use_models=True, day=day)
            self.phase_a.set()
            self.lp.finish()
        except Exception as e:  # noqa: BLE001 - local signals are optional; the engine notes what's missing
            self.error = e
            record.append_now("local.failed", {"evaluation_id": eid, "error": f"{type(e).__name__}: {e}"[:200]},
                              facility_id=fac.id, evaluation_id=eid)
        finally:
            self.phase_a.set()
            self.done.set()

    def wait_phase_a(self) -> list[str]:
        """-> agents still running live (empty: local facts are complete)."""
        self.phase_a.wait(settings.local_agent_timeout + 30)
        pending = list(self.lp.pending) if self.lp and not self.done.is_set() else []
        if not pending:
            self.done.wait(10)
        return pending


def _finalize(rec_id: str, fac: schemas.Facility, eid: str, day: date, why: str, mode: str, local: _LocalJob) -> None:
    """Wait for the live local agents, re-run the rules engine on the complete evidence, and update
    the preliminary recommendation in place. Both versions go to the record."""
    done = _finalizers.setdefault(rec_id, threading.Event())
    try:
        local.done.wait(settings.local_agent_timeout * 2 + 30)
        with SessionLocal() as db:
            row = db.get(models.Recommendation, rec_id)
            evidence = weather_agent.evidence_for(eid)
            final = evaluate(fac, evidence, row.evaluated_at, operating_date=day, target_note=why, target_mode=mode)
            before = {"posture": row.posture, "confidence": row.confidence,
                      "triggers": sorted(t["rule_id"] for t in row.triggers)}
            after = {"posture": final.posture, "confidence": final.confidence,
                     "triggers": sorted(t.rule_id for t in final.triggers)}
            for field in ("posture", "confidence", "confidence_reasons", "what_would_change", "evidence_ids",
                          "lead_time_hours"):
                setattr(row, field, getattr(final, field))
            row.triggers = [t.model_dump() for t in final.triggers]
            row.rationale = row.template_rationale = final.rationale
            row.status = "final"
            record.append(db, "recommendation.finalized", {
                "recommendation_id": rec_id, "evaluation_id": eid, "preliminary": before, "final": after,
                "changed": before != after, "local_error": f"{type(local.error).__name__}" if local.error else None,
            }, facility_id=fac.id, evaluation_id=eid)
            db.commit()
            _after_final(db, row, fac, eid)
    finally:
        done.set()


def wait_final(rec_id: str, timeout: float = 60) -> None:
    ev = _finalizers.get(rec_id)
    if ev is not None:
        ev.wait(timeout)


def _after_final(db: Session, row: models.Recommendation, fac: schemas.Facility, eid: str) -> None:
    """Writer (async), scheduled re-evaluations and message pre-drafting, once the result is final."""
    template = schemas.Recommendation.model_validate(row)
    row.rationale_note = writer_agent.start(
        row.id, template, weather_agent.evidence_for(eid), facility_name=fac.name, tz=fac.timezone,
        state=new_state(evaluation_id=eid, facility_id=fac.id, writer_facility=fac), timeout=settings.agent_timeout)
    db.commit()
    if not fac.temporary:
        facility = db.get(models.Facility, fac.id)
        scheduler.schedule_for(db, facility, row.operating_date)  # 15:00 the day before, 04:00 on the day
        message_agent.start_predraft(row.id)  # wording ready before the manager approves


def recheck(db: Session, facility: models.Facility, day: date, job_key: str | None = None) -> dict:
    """Scheduled re-evaluation: deterministic first (no model calls). Runs the full pipeline with
    agents only if posture, triggers or confidence changed since the latest recommendation."""
    eid = uuid.uuid4().hex
    fac = schemas.Facility.model_validate(facility)
    record.append_now("scheduled.recheck_started", {"evaluation_id": eid, "job": job_key,
                                                    "operating_date": day.isoformat()},
                      facility_id=fac.id, evaluation_id=eid)
    weather_agent.ensure_required_evidence(fac, eid, agent_ran=False, operating_date=day)
    from .sources import airnow
    store_evidence_now(airnow.collect(fac, day), fac.id, eid)
    people = load_inputs(db, facility, day)[0]
    if people:
        exposure.collect(fac.id, eid, people)
        if settings.local_signals:
            alerts, county = _local_prepare(fac, eid, people)
            local_run.run(fac, eid, alerts, people, county, use_models=False, day=day)
    now = utcnow()
    prev = db.scalar(select(models.Recommendation).where(models.Recommendation.scenario_kind.is_(None),
                                                      models.Recommendation.archived_at.is_(None),
                                                      models.Recommendation.facility_id == fac.id,
                                                         models.Recommendation.operating_date == day)
                     .order_by(models.Recommendation.evaluated_at.desc()).limit(1))
    evidence = weather_agent.evidence_for(eid)
    if prev:
        from . import investigations
        _, _, saved = investigations.context(db, prev)
        state = new_state(evaluation_id=eid, facility_id=fac.id, recommendation_id=prev.id,
                          operating_date=day, tools_used=[], tool_lock=threading.Lock())
        evidence = investigations.refresh_expired(investigations.merge_evidence(saved, evidence), state)
    det = evaluate(fac, evidence, now, operating_date=day, target_mode=prev.target_mode if prev else "scheduled",
                   target_note=investigations.original_target_note(db, prev) if prev else None)
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
        if prev:
            # The deterministic recheck already has fresh evidence; don't spend model calls
            # to fetch it again, or replace the calendar explanation with a job label.
            accepted = investigations.request(db, prev, schemas.InvestigationIn(action="recheck_now"),
                                              launch=False, scheduled_job=job_key or "automatic_recheck")
            investigation = db.get(models.Investigation, accepted["investigation_id"])
            revised = investigations.revise(db, investigation, prev, evidence)
            investigation.status = "completed"
            investigation.result = {**investigations.summarize(revised, evidence, schemas.InvestigationIn(action="explain_confidence")),
                "source_label": "Deterministic scheduled recheck", "tools_used": state["tools_used"],
                "new_evidence_ids": [e.id for e in weather_agent.evidence_for(eid)], "target_date": str(day)}
            record.append(db, "investigation.answer", {"investigation_id": investigation.id, **investigation.result},
                          facility_id=fac.id, evaluation_id=investigation.id)
            db.commit()
            result["recommendation_id"] = revised.id
        else:
            full = evaluate_facility(db, facility, target_date=day, scheduled=True)
            result["recommendation_id"] = full.id
    record.append_now("scheduled.recheck", {**result, "job": job_key}, facility_id=fac.id, evaluation_id=eid)
    return result


def _local_prepare(fac: schemas.Facility, eid: str, people: list[schemas.Employee]) -> tuple[list[dict], str | None]:
    """Facility county + employee home counties (corroboration rules); the facility's alerts."""
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
    return alerts or [], county


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

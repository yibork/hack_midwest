"""Scheduled re-evaluations: 15:00 local the day before the target day, and 04:00 on it.

Each job runs the deterministic pipeline first (NWS tools in code, local signals with rule-based
coordination and code extraction: no model calls). Only if the result changed (posture, triggers
or confidence) does it run the full pipeline with agents and store a new recommendation.
"""
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import models, record
from .db import SessionLocal, utcnow

log = logging.getLogger(__name__)
JOBS = (("day_before_1500", -1, time(15, 0)), ("target_0400", 0, time(4, 0)))


def job_times(operating_date: date, tz: str) -> list[tuple[str, datetime]]:
    z = ZoneInfo(tz)
    return [(kind, datetime.combine(operating_date + timedelta(days=offset), at, z)) for kind, offset, at in JOBS]


def schedule_for(db, facility: models.Facility, operating_date: date, now: datetime | None = None) -> list[str]:
    """Create the pending jobs for a target day (idempotent). Past times are not scheduled."""
    now = now or utcnow()
    created = []
    for kind, run_at in job_times(operating_date, facility.timezone):
        key = f"{facility.id}|{operating_date.isoformat()}|{kind}"
        if run_at <= now or db.get(models.ScheduledJob, key):
            continue
        db.add(models.ScheduledJob(key=key, facility_id=facility.id, operating_date=operating_date, kind=kind,
                                   run_at=run_at, status="pending", result={}))
        created.append(key)
    if created:
        record.append(db, "scheduled.jobs_created", {"keys": created}, facility_id=facility.id)
    db.commit()
    return created


def next_run(db, facility_id: str, operating_date: date) -> datetime | None:
    return db.scalar(select(models.ScheduledJob.run_at).where(
        models.ScheduledJob.facility_id == facility_id, models.ScheduledJob.operating_date == operating_date,
        models.ScheduledJob.status == "pending").order_by(models.ScheduledJob.run_at).limit(1))


def tick(now: datetime | None = None) -> list[dict]:
    """Run every due job once. Returns their results (for tests and logs)."""
    from . import pipeline  # pipeline -> services -> scheduler: import at call time

    now = now or utcnow()
    results = []
    with SessionLocal() as db:
        due = db.scalars(select(models.ScheduledJob).where(models.ScheduledJob.status == "pending",
                                                           models.ScheduledJob.run_at <= now)
                         .order_by(models.ScheduledJob.run_at)).all()
        for job in due:
            facility = db.get(models.Facility, job.facility_id)
            if facility is None or job.operating_date < now.astimezone(ZoneInfo(facility.timezone)).date():
                job.status, job.ran_at = "skipped", now
                db.commit()
                continue
            try:
                if job.kind == "notification_send":
                    from .notify import send_notice
                    decision = db.get(models.Decision, job.result.get("decision_id"))
                    notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id)) if decision and not decision.archived_at else None
                    if notice is None:
                        raise ValueError("No active manager-approved notice")
                    rows = send_notice(db, notice.id)
                    result = {"decision_id": decision.id, "notifications": len(rows)}
                elif job.kind == "manager_recheck":
                    from . import investigations, schemas
                    latest = db.scalar(select(models.Recommendation).where(
                        models.Recommendation.facility_id == facility.id,
                        models.Recommendation.operating_date == job.operating_date)
                        .order_by(models.Recommendation.evaluated_at.desc()).limit(1))
                    if latest and latest.status == "preliminary":
                        continue  # retry next tick once the previous check finishes
                    result = investigations.request(db, latest, schemas.InvestigationIn(action="recheck_now"), scheduled_job=job.key) if latest else {"error": "No target-day recommendation"}
                else:
                    result = pipeline.recheck(db, facility, job.operating_date, job_key=job.key)
                job.status = "done"
            except Exception as e:  # noqa: BLE001 - keep the scheduler alive; the job records the failure
                log.exception("scheduled job %s failed", job.key)
                result, job.status = {"error": f"{type(e).__name__}: {e}"[:300]}, "failed"
            job.ran_at, job.result = utcnow(), result
            db.commit()
            results.append({"key": job.key, **result})
    return results

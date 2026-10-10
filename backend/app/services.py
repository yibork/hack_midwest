"""Glue between sources, rules engine, writer and the database. Each step writes RecordEvents."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, record, schemas
from .db import SessionLocal, utcnow
from .notify import select_recipients


def store_evidence(db: Session, evidence: list[schemas.Evidence], facility_id: str | None,
                   evaluation_id: str | None = None) -> None:
    for ev in evidence:
        ev.facility_id = facility_id
        ev.evaluation_id = evaluation_id
        db.add(models.Evidence(**ev.model_dump()))
        record.append(db, "evidence.fetched", {
            "evidence_id": ev.id, "source": ev.source, "kind": ev.kind, "status": ev.status,
            "url": ev.url, "snapshot_hash": ev.snapshot_hash, "relevance_note": ev.relevance_note,
        }, facility_id=facility_id, timestamp=ev.retrieved_at, evaluation_id=evaluation_id)


def store_evidence_now(evidence: list[schemas.Evidence], facility_id: str | None,
                       evaluation_id: str | None) -> None:
    """Persist and commit in a separate session (agent tools run in worker threads)."""
    with SessionLocal() as s:
        store_evidence(s, evidence, facility_id, evaluation_id)
        s.commit()


def decide_by(db: Session, facility: models.Facility, rec: models.Recommendation) -> datetime | None:
    """Earliest typical departure among included employees, minus 30 minutes."""
    recipients = [r for r in select_recipients(db, facility, rec.posture, rec.operating_date) if r.included]
    if not recipients:
        return None
    earliest = min(r.typical_departure_time for r in recipients)  # "HH:MM" sorts correctly
    h, m = map(int, earliest.split(":"))
    local = datetime(rec.operating_date.year, rec.operating_date.month, rec.operating_date.day, h, m,
                     tzinfo=ZoneInfo(facility.timezone))
    return local - timedelta(minutes=30)


def recommendation_detail(db: Session, rec: models.Recommendation) -> schemas.RecommendationDetail:
    facility = db.get(models.Facility, rec.facility_id)
    evidence = db.scalars(select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids))).all()
    decision = db.scalar(select(models.Decision).where(models.Decision.recommendation_id == rec.id))
    return schemas.RecommendationDetail(
        **schemas.Recommendation.model_validate(rec).model_dump(),
        facility=schemas.Facility.model_validate(facility),
        evidence=[schemas.Evidence.model_validate(e) for e in sorted(evidence, key=lambda e: e.retrieved_at)],
        decision=schemas.Decision.model_validate(decision) if decision else None,
        decide_by=decide_by(db, facility, rec),
    )


def record_decision(db: Session, rec: models.Recommendation, body: schemas.DecisionIn) -> models.Decision:
    final = rec.posture if body.action == "approve" else body.final_posture
    d = models.Decision(
        id=uuid.uuid4().hex, recommendation_id=rec.id, manager_name=body.manager_name.strip(),
        action=body.action, final_posture=final, reason=(body.reason or "").strip() or None,
        decided_at=utcnow(),
    )
    db.add(d)
    record.append(db, "decision.recorded", {
        "decision_id": d.id, "recommendation_id": rec.id, "manager_name": d.manager_name,
        "action": d.action, "recommended_posture": rec.posture, "final_posture": d.final_posture,
        "reason": d.reason,
    }, facility_id=rec.facility_id, evaluation_id=rec.evaluation_id)
    db.commit()
    return d

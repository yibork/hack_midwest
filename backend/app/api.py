"""All HTTP routes, mounted under /api."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy.exc import IntegrityError

from . import models, pipeline, record, schemas, services
from .agents import notify_agent
from .db import get_db
from .models import RecordEvent
from .notify import NoDecisionError, recipients_for_decision
from .sources import nws

router = APIRouter(prefix="/api")


def _get_or_404(db: Session, model, id_: str):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t and t.tzinfo is None else t


@router.get("/health")
def health():
    from .agents.ratelimit import last_call
    from .config import settings
    from .db import check_database, engine
    from .seed import ensure_seeded

    problem = settings.inference_problem()
    return {
        "status": "ok",
        "database": "ok" if ensure_seeded() and check_database() else "unreachable",
        "database_backend": engine.dialect.name,  # "postgresql" or "sqlite"; never the URL
        "inference": "missing" if problem else "configured",
        "inference_problem": problem,  # never contains the key
        "model": settings.chat_model,
        "last_model_call": last_call(),  # {ok, at, error}; ok is null until the first call
    }


# ---------------------------------------------------------------- facilities

@router.get("/facilities", response_model=list[schemas.Facility])
def list_facilities(db: Session = Depends(get_db)):
    return db.scalars(select(models.Facility).where(models.Facility.temporary.is_(False))
                      .order_by(models.Facility.name)).all()


@router.get("/facilities/{facility_id}", response_model=schemas.Facility)
def get_facility(facility_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Facility, facility_id)


@router.get("/facilities/{facility_id}/latest-recommendation",
            response_model=schemas.RecommendationDetail | None)
def latest_recommendation(facility_id: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Facility, facility_id)
    rec = db.scalar(select(models.Recommendation).where(models.Recommendation.facility_id == facility_id)
                    .order_by(models.Recommendation.evaluated_at.desc()).limit(1))
    return services.recommendation_detail(db, rec) if rec else None


def _check_new_evaluation_id(db: Session, evaluation_id: str | None) -> None:
    if evaluation_id and db.scalar(select(models.Recommendation)
                                   .where(models.Recommendation.evaluation_id == evaluation_id)):
        raise HTTPException(409, f"evaluation {evaluation_id} already exists")


@router.post("/facilities/{facility_id}/evaluate", response_model=schemas.RecommendationDetail)
def evaluate_facility(facility_id: str, body: schemas.EvaluateIn | None = None, db: Session = Depends(get_db)):
    facility = _get_or_404(db, models.Facility, facility_id)
    eid = body.evaluation_id if body else None
    _check_new_evaluation_id(db, eid)
    try:
        rec = pipeline.evaluate_facility(db, facility, eid)
    except IntegrityError:
        raise HTTPException(409, f"evaluation {eid} already exists")
    return services.recommendation_detail(db, rec)


@router.post("/evaluate-point", response_model=schemas.RecommendationDetail)
def evaluate_point(body: schemas.EvaluatePointIn, db: Session = Depends(get_db)):
    _check_new_evaluation_id(db, body.evaluation_id)
    rec = pipeline.evaluate_point(db, body.lat, body.lon, body.name, body.evaluation_id)
    return services.recommendation_detail(db, rec)


# ---------------------------------------------------------------- recommendations & decisions

@router.get("/recommendations/{rec_id}", response_model=schemas.RecommendationDetail)
def get_recommendation(rec_id: str, db: Session = Depends(get_db)):
    return services.recommendation_detail(db, _get_or_404(db, models.Recommendation, rec_id))


@router.post("/recommendations/{rec_id}/decision", response_model=schemas.Decision)
def decide(rec_id: str, body: schemas.DecisionIn, db: Session = Depends(get_db)):
    rec = _get_or_404(db, models.Recommendation, rec_id)
    if db.scalar(select(models.Decision).where(models.Decision.recommendation_id == rec_id)):
        raise HTTPException(409, "A decision already exists for this recommendation; re-evaluate to decide again.")
    return services.record_decision(db, rec, body)


@router.get("/decisions/{decision_id}", response_model=schemas.Decision)
def get_decision(decision_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Decision, decision_id)


@router.get("/decisions/{decision_id}/recipients", response_model=list[schemas.Recipient])
def recipients(decision_id: str, db: Session = Depends(get_db)):
    decision = _get_or_404(db, models.Decision, decision_id)
    return recipients_for_decision(db, decision)[2]


@router.post("/decisions/{decision_id}/notify", response_model=list[schemas.Notification])
def notify(decision_id: str, db: Session = Depends(get_db)):
    try:
        return notify_agent.notify(db, decision_id)
    except NoDecisionError as e:
        raise HTTPException(404, str(e))


@router.get("/decisions/{decision_id}/notifications", response_model=list[schemas.Notification])
def notifications(decision_id: str, db: Session = Depends(get_db)):
    return db.scalars(select(models.Notification).where(models.Notification.decision_id == decision_id)).all()


# ---------------------------------------------------------------- national scan

@router.get("/national-scan", response_model=list[schemas.NationalAlert])
def national_scan(db: Session = Depends(get_db)):
    alerts, evidence = nws.get_active_alerts_national(limit=20)
    services.store_evidence(db, evidence, facility_id=None)
    db.commit()
    if not alerts and evidence[0].status == "unknown":
        raise HTTPException(502, f"NWS national alerts unavailable: {evidence[0].value.get('error')}")
    return alerts


# ---------------------------------------------------------------- record / replay

@router.get("/record", response_model=list[schemas.RecordEvent])
def get_record(facility_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
               limit: int | None = None, newest_first: bool = False, db: Session = Depends(get_db)):
    return record.list_events(db, facility_id, _aware(since), _aware(until), limit, newest_first)


@router.get("/agent-activity", response_model=list[schemas.RecordEvent])
def agent_activity(evaluation_id: str | None = None, since: datetime | None = None,
                   facility_id: str | None = None, limit: int = 500, db: Session = Depends(get_db)):
    """Evaluation-scoped RecordEvents (agent calls, tool calls, evidence, fallbacks, guard blocks)
    in order. Poll with evaluation_id while an evaluation runs."""
    q = select(RecordEvent).where(RecordEvent.evaluation_id.is_not(None))
    if evaluation_id:
        q = q.where(RecordEvent.evaluation_id == evaluation_id)
    if facility_id:
        q = q.where(RecordEvent.facility_id == facility_id)
    if since:
        q = q.where(RecordEvent.timestamp >= _aware(since))
    return db.scalars(q.order_by(RecordEvent.timestamp, RecordEvent.id).limit(limit)).all()


@router.get("/record/at", response_model=schemas.RecordAt)
def record_at(facility_id: str, timestamp: datetime, db: Session = Depends(get_db)):
    ts = _aware(timestamp)
    rec = db.scalar(select(models.Recommendation)
                    .where(models.Recommendation.facility_id == facility_id,
                           models.Recommendation.evaluated_at <= ts)
                    .order_by(models.Recommendation.evaluated_at.desc()).limit(1))
    detail, notes = None, []
    if rec:
        detail = services.recommendation_detail(db, rec)
        # Only show the decision if it had been made by that moment.
        if detail.decision and detail.decision.decided_at > ts:
            detail.decision = None
        if detail.decision:
            notes = db.scalars(select(models.Notification).where(
                models.Notification.decision_id == detail.decision.id,
                models.Notification.sent_at <= ts)).all()
    return schemas.RecordAt(as_of=ts, facility_id=facility_id, recommendation=detail,
                            notifications=[schemas.Notification.model_validate(n) for n in notes])

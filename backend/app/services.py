"""Glue between sources, rules engine, writer and the database. Each step writes RecordEvents."""
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, record, schemas, scheduler
from .db import SessionLocal, utcnow


def store_evidence(db: Session, evidence: list[schemas.Evidence], facility_id: str | None,
                   evaluation_id: str | None = None) -> None:
    """Store Evidence rows. Within an evaluation, a response already stored (same kind and snapshot
    hash, e.g. a tool the agent ran before it timed out and the fallback runs again) is reused:
    ev.id is set to the existing row and nothing is duplicated."""
    for ev in evidence:
        ev.facility_id = facility_id
        ev.evaluation_id = evaluation_id
        if evaluation_id and ev.snapshot_hash:
            db.flush()
            same = db.scalar(select(models.Evidence).where(
                models.Evidence.evaluation_id == evaluation_id, models.Evidence.kind == ev.kind,
                models.Evidence.snapshot_hash == ev.snapshot_hash).limit(1))
            if same is not None:
                ev.id = same.id
                continue
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


def people_summary(db: Session, rec: models.Recommendation, facility: models.Facility) -> schemas.PeopleSummary | None:
    """Coverage, decision clock and exposure for the recommended posture (default parameters)."""
    if facility.temporary:
        return None
    from .agents.local.facts import RAMP
    from .notify.notices import plan_for

    plan = plan_for(db, facility, rec, rec.posture)
    summary = plan.summary()
    # Reconcile with road facts: conditions (not closures) inside commute corridors are minor.
    minor = [e.value for e in db.scalars(select(models.Evidence).where(
        models.Evidence.id.in_(rec.evidence_ids), models.Evidence.kind == "local_fact"))
        if isinstance(e.value, dict) and e.value.get("fact_type") == "road_condition"
        and e.value.get("affected_employee_ids")]
    if minor:
        commuting = {i.employee.id for i in plan.impacts if i.onsite_expected and not i.exposed}
        touched = commuting & {eid for v in minor for eid in v["affected_employee_ids"]}
        ramps = all(RAMP.search(f"{v.get('title', '')} {v.get('supporting_quote', '')}") for v in minor)
        summary.minor_commute_count = len(touched)
        summary.minor_commute_label = "ramp closures" if ramps else "minor road conditions"
    return summary


def recommendation_detail(db: Session, rec: models.Recommendation) -> schemas.RecommendationDetail:
    facility = db.get(models.Facility, rec.facility_id)
    evidence = db.scalars(select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids))).all()
    decision = db.scalar(select(models.Decision).where(models.Decision.recommendation_id == rec.id))
    people = people_summary(db, rec, facility)
    return schemas.RecommendationDetail(
        **schemas.Recommendation.model_validate(rec).model_dump(),
        change_rule_ids=[change_rule_id(line) for line in rec.what_would_change],
        facility=schemas.Facility.model_validate(facility),
        evidence=[schemas.Evidence.model_validate(e) for e in sorted(evidence, key=lambda e: e.retrieved_at)],
        decision=schemas.Decision.model_validate(decision) if decision else None,
        decide_by=people.clock.at if people else None,
        people=people,
        next_reevaluation=scheduler.next_run(db, rec.facility_id, rec.operating_date),
    )


def change_rule_id(line: str) -> str:
    """Resolve saved explanation strings to policy anchors, including old records."""
    if "access_route_closed" in line or "Reopening" in line:
        return "access_route_closed"
    if "site_hazard_reported" in line:
        return "site_hazard_reported"
    if "58 mph" in line or "Sustained winds reaching 40 mph" in line or "High Wind" in line or "Extreme Wind" in line:
        return "high_wind"
    if "46 mph" in line or "Sustained winds reaching 31 mph" in line or "Wind Advisory" in line:
        return "wind_advisory"
    if "Flood" in line:
        return "flood"
    if "Thunderstorm" in line or "Tornado" in line:
        return "convective"
    if "Extreme" in line:
        return "extreme_immediate_alert"
    return "nws_cap"


def lead_time_hours(db: Session, rec: models.Recommendation, at) -> float:
    from .rules.engine import compute_windows, load_policy

    facility = schemas.Facility.model_validate(db.get(models.Facility, rec.facility_id))
    w = compute_windows(facility, at, load_policy(), rec.operating_date)
    return round((w.commute_start - at).total_seconds() / 3600, 1)


def record_decision(db: Session, rec: models.Recommendation, body: schemas.DecisionIn) -> models.Decision:
    final = rec.posture if body.action == "approve" else body.final_posture
    d = models.Decision(
        id=uuid.uuid4().hex, recommendation_id=rec.id, manager_name=body.manager_name.strip(),
        action=body.action, final_posture=final, reason=(body.reason or "").strip() or None,
        decided_at=utcnow(), delay_hours=body.delay_hours, dismissal_time=body.dismissal_time,
    )
    db.add(d)
    record.append(db, "decision.recorded", {
        "decision_id": d.id, "recommendation_id": rec.id, "manager_name": d.manager_name,
        "action": d.action, "recommended_posture": rec.posture, "final_posture": d.final_posture,
        "reason": d.reason, "delay_hours": d.delay_hours, "dismissal_time": d.dismissal_time,
        "operating_date": rec.operating_date,
        # Hours from the decision to the start of the target day's commute window.
        "lead_time_hours": lead_time_hours(db, rec, d.decided_at),
    }, facility_id=rec.facility_id, evaluation_id=rec.evaluation_id)
    db.commit()
    facility = db.get(models.Facility, rec.facility_id)
    if not facility.temporary:
        from .notify import create_notice

        create_notice(db, d)  # plan + messages ready, nothing sent until the manager sends
    return d

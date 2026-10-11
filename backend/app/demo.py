"""DEMO_MODE scenarios and non-destructive resets. No model calls for synthetic/replay data."""
import json
import threading
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, pipeline, record, schemas, services
from .config import settings
from .db import get_db, utcnow
from .roles import manager_only
from .rules.engine import compute_windows, evaluate, load_policy
from .seed import FACILITY_ID, PERSONAS, emp_id

lock = threading.RLock()
router = APIRouter(prefix="/api/demo")
LABELS = {"live_next": "Live: KC facility, next operating day",
          "live_today": "Live: KC facility, today as a workday (DEMO)",
          "live_national": "Live: most severe active US alert",
          "simulated_winter": "SIMULATED: winter storm · synthetic 04:00 demo clock"}


def enabled():
    if not settings.demo_mode:
        raise HTTPException(403, "Requires DEMO_MODE=1")


def state(db):
    row = db.get(models.DemoState, FACILITY_ID)
    if row is None:
        row = models.DemoState(facility_id=FACILITY_ID, mode="live_next", label=LABELS["live_next"])
        db.add(row)
        db.flush()
    return row


def active_recommendation(db, facility_id):
    current = db.get(models.DemoState, FACILITY_ID) if settings.demo_mode else None
    if not current or facility_id not in {FACILITY_ID, current.active_facility_id} or not current.recommendation_id:
        return None
    rec = db.get(models.Recommendation, current.recommendation_id)
    while rec and not rec.archived_at:
        child = db.scalar(select(models.Recommendation).where(models.Recommendation.previous_recommendation_id == rec.id,
                                                              models.Recommendation.archived_at.is_(None)))
        if not child:
            return rec
        rec = child
    return None


@router.get("/state")
def get_state(db: Session = Depends(get_db)):
    row = db.get(models.DemoState, FACILITY_ID) if settings.demo_mode else None
    return {"enabled": settings.demo_mode, "mode": row.mode if row else "live_next",
            "label": row.label if row else LABELS["live_next"],
            "recommendation_id": row.recommendation_id if row else None,
            "facility_id": (row.active_facility_id if row else None) or FACILITY_ID,
            "captured_at": row.captured_at if row else None}


@router.get("/recordings", dependencies=[Depends(manager_only), Depends(enabled)])
def recordings(db: Session = Depends(get_db)):
    facility_ids = [FACILITY_ID] + list(db.scalars(select(models.Facility.id).where(models.Facility.demo_origin_id == FACILITY_ID)))
    rows = db.scalars(select(models.Recommendation).where(models.Recommendation.facility_id.in_(facility_ids))
                      .order_by(models.Recommendation.evaluated_at.desc()).limit(100)).all()
    return [{"id": r.id, "captured_at": r.evaluated_at, "operating_date": r.operating_date,
             "posture": r.posture, "label": f"{r.scenario_kind or 'live'} · {r.evaluated_at:%a %b %d, %H:%M UTC}"}
            for r in rows if r.evidence_ids and r.scenario_kind != "replay" and r.status != "preliminary"]


class ScenarioIn(BaseModel):
    mode: str
    recording_id: str | None = None
    alert_id: str | None = None


@router.get("/national-alerts", dependencies=[Depends(manager_only), Depends(enabled)])
def national_alerts(db: Session = Depends(get_db)):
    from .sources import nws
    alerts, evidence = nws.get_active_alerts_national(limit=5, demo_only=True)
    services.store_evidence(db, evidence, FACILITY_ID, None)
    db.commit()
    return {"alerts": alerts, "retrieved_at": evidence[0].retrieved_at,
            "status": evidence[0].status, "snapshot_hash": evidence[0].snapshot_hash,
            "note": "Supported hazards, Moderate or higher, ongoing or starting within 24 hours; severity, urgency, certainty order."}


def synthetic(db, facility):
    now = utcnow()
    fac = schemas.Facility.model_validate(facility)
    day = now.astimezone(ZoneInfo(fac.timezone)).date()
    clock = datetime.combine(day, time(4), ZoneInfo(fac.timezone))
    w = compute_windows(fac, clock, load_policy(), day)
    warning = {"event": "Winter Storm Warning", "severity": "Severe", "urgency": "Expected",
               "certainty": "Likely", "onset": w.commute_start.isoformat(),
               "ends": w.departure_end.isoformat(), "headline": "SIMULATED winter storm covering the facility"}
    rows = [("active_alerts", {"alerts": [warning]}),
            ("wind_forecast", {"hours": [{"time": (w.commute_start + timedelta(minutes=30 * i)).isoformat(),
                                         "gust_mph": 65, "wind_mph": 45, "ice_inches": 0.5,
                                         "short_forecast": "SIMULATED freezing rain"} for i in range(4)]}),
            ("home_zone_alerts", {"zones": {"MOZ028": [warning], "MOC165": [warning]}, "errors": {}}),
            ("local_fact", {"authority": "official_state", "fact_type": "road_closure",
                "agent": "roads", "source_id": "demo", "category": "weather", "distance_km": 0.9,
                "item_id": "simulated-i70", "segments": ["I-70 downtown"], "link": "",
                "points": [[39.10702, -94.58315]], "geometry": {"type": "Point", "coordinates": [-94.58315, 39.10702]},
                "title": "SIMULATED I-70 access segment closed", "supporting_quote": "SIMULATED full I-70 closure",
                "source_name": "SIMULATED state DOT premise (not a real DOT report)", "confidence": "high",
                "routes_mentioned": ["I-70"], "matches_facility_access_route": True,
                "location_provenance": "source_coordinates", "relevance": "facility",
                "lat": 39.10702, "lon": -94.58315, "distance_from_facility_km": 0.9,
                "effective_from": w.commute_start.isoformat(), "effective_until": w.commute_end.isoformat(),
                "affected_employee_ids": [], "matches_employee_commute_routes": [], "simulated": True})]
    eid = uuid.uuid4().hex
    evidence = [schemas.Evidence(id=uuid.uuid4().hex, source="demo", kind=kind,
                value={**value, "label": "SIMULATED", "simulated": True}, retrieved_at=now,
                source_time=now, valid_until=now + timedelta(hours=24), status="ok",
                scenario_id=eid, scenario_kind="simulated",
                snapshot_hash=record.save_snapshot(json.dumps({"label": "SIMULATED", **value}, sort_keys=True).encode()),
                relevance_note="SIMULATED demo input, not a live government report. Ice is context; wind exceeds the policy closure threshold.")
                for kind, value in rows]
    services.store_evidence(db, evidence, facility.id, eid)
    result = evaluate(fac, evidence, clock, operating_date=day, target_mode="demo_workday",
                      target_note=f"SIMULATED: today ({day}) treated as a workday; demo clock 04:00 local.")
    result.evaluated_at = now
    result.evaluation_id = eid
    result.scenario_id, result.scenario_kind = eid, "simulated"
    result.scenario_context = {"clock": clock.isoformat(), "label": LABELS["simulated_winter"]}
    result.rationale = "SIMULATED. " + result.rationale
    for trigger in result.triggers:
        trigger.description = "SIMULATED: " + trigger.description
    rec = models.Recommendation(**{**result.model_dump(), "status": "final", "template_rationale": result.rationale})
    db.add(rec)
    db.flush()
    record.append(db, "evaluation.completed", {"recommendation_id": rec.id, "posture": rec.posture,
                  "scenario": "SIMULATED", "operating_date": day, "demo_clock": clock.isoformat()},
                  facility_id=facility.id, evaluation_id=eid)
    return rec


def replay(db, facility, recording_id):
    saved = db.get(models.Recommendation, recording_id)
    if saved is None or saved.facility_id != FACILITY_ID or saved.status == "preliminary":
        raise HTTPException(404, "Recording not found")
    eid = uuid.uuid4().hex
    evidence, mapping = [], {}
    for row in db.scalars(select(models.Evidence).where(models.Evidence.id.in_(saved.evidence_ids))):
        e = schemas.Evidence.model_validate(row).model_copy(update={"id": uuid.uuid4().hex,
            "scenario_id": eid, "scenario_kind": "replay", "evaluation_id": eid,
            "relevance_note": f"REPLAY: originally captured {row.retrieved_at.isoformat()}. {row.relevance_note}"})
        evidence.append(e)
        mapping[row.id] = e.id
    services.store_evidence(db, evidence, facility.id, eid)
    values = schemas.Recommendation.model_validate(saved).model_dump()
    values.update(id=uuid.uuid4().hex, evaluation_id=eid, evaluated_at=utcnow(), scenario_id=eid,
                  scenario_kind="replay", evidence_ids=[e.id for e in evidence],
                  scenario_context={"clock": (saved.scenario_context or {}).get("clock", saved.evaluated_at.isoformat()),
                                    "captured_at": saved.evaluated_at.isoformat(), "recording_id": saved.id},
                  rationale="REPLAY (not current conditions). " + saved.rationale)
    values["status"] = "final"
    values["triggers"] = [{**t, "evidence_id": mapping.get(t.get("evidence_id"), t.get("evidence_id")),
                           "description": "REPLAY: " + t["description"]} for t in values["triggers"]]
    rec = models.Recommendation(**values)
    db.add(rec)
    db.flush()
    record.append(db, "demo.replay", {"recommendation_id": rec.id, "recording_id": saved.id,
                  "label": "REPLAY", "captured_at": saved.evaluated_at}, facility_id=facility.id, evaluation_id=eid)
    return rec


@router.post("/scenario", dependencies=[Depends(manager_only), Depends(enabled)])
def set_scenario(body: ScenarioIn, db: Session = Depends(get_db)):
    with lock:
        if body.mode not in {*LABELS, "replay"}:
            raise HTTPException(422, "Unknown scenario")
        facility = db.get(models.Facility, FACILITY_ID)
        if body.mode == "simulated_winter":
            rec = synthetic(db, facility)
        elif body.mode == "replay":
            saved = db.get(models.Recommendation, body.recording_id) if body.recording_id else None
            if saved:
                saved_facility = db.get(models.Facility, saved.facility_id)
                if saved_facility and saved_facility.demo_origin_id == FACILITY_ID:
                    facility = saved_facility
            rec = replay(db, facility, body.recording_id)
        elif body.mode == "live_national":
            from .sources import nws
            from .demo_location import relocate
            selected_id = body.alert_id
            if not selected_id:
                saved = db.get(models.Recommendation, state(db).recommendation_id) if state(db).recommendation_id else None
                selected_id = (saved.scenario_context or {}).get("alert_id") if saved else None
            if not selected_id:
                raise HTTPException(422, "Scan real alerts and choose a location first")
            alerts, evidence = nws.get_active_alerts_national(limit=5, demo_only=True)
            found = next((a for a in alerts if a.id == selected_id and a.lat is not None and a.lon is not None), None)
            if found is None:
                raise HTTPException(409, "Selected alert is no longer in the eligible top five or has no land location; refresh the scan")
            try:
                rec = relocate(db, facility, found, evidence)
            except ValueError as exc:
                db.rollback()
                raise HTTPException(502, str(exc)) from exc
        else:
            rec = pipeline.evaluate_facility(db, facility, demo_workday=body.mode == "live_today")
        current = state(db)
        current.mode, current.recommendation_id, current.active_facility_id = body.mode, rec.id, rec.facility_id
        current.label = rec.scenario_context["label"] if body.mode == "live_national" else LABELS.get(body.mode, "REPLAY: saved conditions, not current weather")
        current.captured_at = datetime.fromisoformat(rec.scenario_context["captured_at"]) if body.mode == "replay" else None
        record.append(db, "demo.scenario_changed", {"mode": body.mode, "label": current.label,
                      "recommendation_id": rec.id}, facility_id=FACILITY_ID)
        db.commit()
        return services.recommendation_detail(db, rec)


@router.post("/reset", dependencies=[Depends(manager_only), Depends(enabled)])
def reset(db: Session = Depends(get_db)):
    from .investigations import _run_lock
    from .notify.notices import _send_lock
    # Join running investigations and dispatches before archiving pending state.
    with _run_lock, _send_lock, lock:
        at = utcnow()
        facility_ids = [FACILITY_ID] + list(db.scalars(select(models.Facility.id).where(models.Facility.demo_origin_id == FACILITY_ID)))
        recs = list(db.scalars(select(models.Recommendation).where(models.Recommendation.facility_id.in_(facility_ids))))
        rec_ids = [r.id for r in recs]
        decisions = list(db.scalars(select(models.Decision).where(models.Decision.recommendation_id.in_(rec_ids))))
        notices = list(db.scalars(select(models.Notice).where(models.Notice.facility_id.in_(facility_ids))))
        investigations = list(db.scalars(select(models.Investigation).where(models.Investigation.recommendation_id.in_(rec_ids))))
        groups = {"recommendations": recs, "decisions": decisions, "notices": notices,
                  "notifications": list(db.scalars(select(models.Notification).where(models.Notification.notice_id.in_([n.id for n in notices])))),
                  "investigations": investigations,
                  "staff_responses": list(db.scalars(select(models.StaffQuestion).where(models.StaffQuestion.investigation_id.in_([i.id for i in investigations]))))}
        counts = {}
        for name, rows in groups.items():
            active = [r for r in rows if r.archived_at is None]
            counts[name] = len(active)
            for row in active:
                row.archived_at = at
        jobs = db.scalars(select(models.ScheduledJob).where(models.ScheduledJob.facility_id.in_(facility_ids),
                                                           models.ScheduledJob.status == "pending")).all()
        for job in jobs:
            job.status, job.ran_at, job.result = "skipped", at, {"reason": "demo_reset"}
        current = state(db)
        current.mode, current.label = "live_next", LABELS["live_next"]
        current.recommendation_id = current.active_facility_id = current.captured_at = None
        record.append(db, "demo_reset", {"archived": counts, "timers_cancelled": counts["notifications"],
                      "scheduled_jobs_cancelled": len(jobs), "label": "DEMO; historical evidence and record retained"}, facility_id=FACILITY_ID)
        db.commit()
        return {"archived": counts, "recordings_retained": True}


@router.get("/delivery-status", dependencies=[Depends(manager_only), Depends(enabled)])
def delivery_status(db: Session = Depends(get_db)):
    result = []
    for key in PERSONAS:
        person = db.get(models.Employee, emp_id(key))
        if person is None:
            continue
        candidates = []
        for row in db.scalars(select(models.Notification).where(models.Notification.employee_id == person.id)):
            push = (row.channel_results or {}).get("push")
            if push:
                candidates.append({"at": push.get("sent_at"), "ok": push.get("ok"), "error": push.get("error"), "kind": "Notice"})
        for row in db.scalars(select(models.StaffQuestion).where(models.StaffQuestion.employee_id == person.id)):
            if row.delivery:
                candidates.append({"at": row.delivery.get("sent_at") or row.expires_at.isoformat(),
                                   "ok": row.delivery.get("ok"), "error": row.delivery.get("error"), "kind": "Staff question"})
        result.append({"employee_id": person.id, "ntfy_topic": person.ntfy_topic,
                       "last_delivery": max(candidates, key=lambda x: x["at"] or "") if candidates else None})
    return result

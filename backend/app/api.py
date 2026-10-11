"""All HTTP routes, mounted under /api."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy.exc import IntegrityError

from . import models, people_api, pipeline, record, schemas, services
from .db import get_db
from .config import settings
from .models import RecordEvent
from .notify import NoDecisionError, send_notice
from .roles import manager_only, manager_or_auditor_read, viewer, Viewer, audit_payload
from .sources import nws

# Manager routes. Role-specific routes (team, access list, my status) are in people_api.py.
router = APIRouter(prefix="/api", dependencies=[Depends(manager_or_auditor_read)])
public = APIRouter(prefix="/api")


@router.get("/thresholds")
def thresholds():
    from .rules.engine import policy_catalog
    return policy_catalog()


@router.get("/evidence/{id}", response_model=schemas.Evidence)
def investigation_evidence(id: str, db: Session = Depends(get_db), v: Viewer = Depends(viewer)):
    ev = schemas.Evidence.model_validate(_get_or_404(db, models.Evidence, id))
    if v.role == "auditor":
        ev.value = audit_payload(ev.value)
    return ev


@router.post("/recommendations/{id}/investigate", status_code=202)
def investigate(id: str, body: schemas.InvestigationIn, db: Session = Depends(get_db)):
    from . import investigations
    return investigations.request(db, _get_or_404(db, models.Recommendation, id), body)


@router.post("/recommendations/{id}/collect-evidence", status_code=202)
def collect_evidence(id: str, db: Session = Depends(get_db)):
    from . import sweep
    return sweep.request(db, _get_or_404(db, models.Recommendation, id))


@router.get("/investigations/{id}")
def investigation_result(id: str, db: Session = Depends(get_db)):
    from . import investigations
    return investigations.detail(db, _get_or_404(db, models.Investigation, id))


@router.get("/recommendations/{id}/suggestions")
def recommendation_suggestions(id: str, db: Session = Depends(get_db)):
    from . import investigations
    rec = _get_or_404(db, models.Recommendation, id)
    _, _, evidence = investigations.context(db, rec)
    return {"suggestions": investigations.suggestions(rec, evidence), "budget": investigations.budget(db, rec)}


@router.get("/recommendations/{id}/investigations")
def investigation_thread(id: str, db: Session = Depends(get_db)):
    from . import investigations
    rec = _get_or_404(db, models.Recommendation, id)
    key = investigations.root_key(db, rec)
    rows = db.scalars(select(models.Investigation).where(models.Investigation.budget_key == key)
                      .order_by(models.Investigation.created_at)).all()
    return [investigations.detail(db, row) for row in rows]


@router.get("/recommendations/{id}/staff-candidates")
def investigation_staff_candidates(id: str, db: Session = Depends(get_db)):
    from . import investigations
    rec = _get_or_404(db, models.Recommendation, id)
    people = investigations.staff_candidates(db, rec, schemas.InvestigationIn(action="ask_staff", role="facility_ops", question="Site check"))
    return [{"id": p.id, "name": p.name, "role": p.role} for p in people]


@router.post("/staff-questions/{id}/demo-response")
def demo_staff_response(id: str, body: dict, db: Session = Depends(get_db)):
    from . import investigations
    from .config import settings
    if not settings.demo_mode:
        raise HTTPException(403, "Answering on a persona's behalf requires DEMO_MODE=1")
    with investigations._run_lock:
        return investigations.submit_staff(db, _get_or_404(db, models.StaffQuestion, id), body.get("option"), demo=True)


def _staff_token(db, token):
    import hashlib
    row = db.scalar(select(models.StaffQuestion).where(models.StaffQuestion.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if row is None:
        raise HTTPException(404, "Unknown staff request")
    if row.responded_at:
        raise HTTPException(409, "This request has already been answered")
    from .db import utcnow
    if row.expires_at <= utcnow():
        raise HTTPException(410, "This request expired")
    return row


@public.get("/staff-responses/{token}", response_class=HTMLResponse)
def staff_response_form(token: str, db: Session = Depends(get_db)):
    import html
    from .config import settings
    row = _staff_token(db, token)
    investigation = db.get(models.Investigation, row.investigation_id)
    rec = db.get(models.Recommendation, investigation.recommendation_id)
    # Opening the link never submits a reply; confirmation requires a POST.
    buttons = ''.join(f'<button name="option" value="{html.escape(option, quote=True)}">{html.escape(option)}</button> ' for option in row.options)
    return HTMLResponse(f'<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Site evidence request</title></head><body><h1>Site evidence for {rec.operating_date}</h1><p>{html.escape(row.question)}</p><p>Unsafe means an unsafe site condition during that day’s commute window.</p><form method="post" action="{settings.public_base_url}/api/staff-responses/{token}">{buttons}</form></body></html>', headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


@public.post("/staff-responses/{token}")
async def staff_response(token: str, req: Request, db: Session = Depends(get_db)):
    if req.headers.get("X-Role") == "auditor" or req.query_params.get("role") == "auditor":
        raise HTTPException(403, "Read-only auditor cannot submit staff evidence")
    from . import investigations
    from urllib.parse import parse_qs
    raw = await req.body()
    if len(raw) > 1000:
        raise HTTPException(413, "Reply is too large")
    if "application/json" in req.headers.get("content-type", ""):
        import json
        try:
            option = json.loads(raw).get("option")
        except (ValueError, AttributeError):
            raise HTTPException(422, "Invalid reply")
    else:
        option = parse_qs(raw.decode()).get("option", [None])[0]
    from starlette.concurrency import run_in_threadpool
    def submit():
        with investigations._run_lock:
            return investigations.submit_staff(db, _staff_token(db, token), option)
    result = await run_in_threadpool(submit)
    if "application/json" not in req.headers.get("content-type", ""):
        return HTMLResponse("<h1>Thank you</h1><p>Your site report was recorded.</p>", headers={"Cache-Control": "no-store"})
    return result


def _get_or_404(db: Session, model, id_: str):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t and t.tzinfo is None else t


@public.get("/health")
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
        "demo_mode": settings.demo_mode,
        "last_model_call": last_call(),  # {ok, at, error}; ok is null until the first call
    }


# ---------------------------------------------------------------- facilities

@public.get("/facilities", response_model=list[schemas.Facility])
def list_facilities(db: Session = Depends(get_db)):
    facilities = db.scalars(select(models.Facility).where(models.Facility.temporary.is_(False), models.Facility.demo_origin_id.is_(None))
                      .order_by(models.Facility.name)).all()
    if settings.demo_mode:
        current = db.get(models.DemoState, "usda-kc-demo")
        active = db.get(models.Facility, current.active_facility_id) if current and current.active_facility_id else None
        if active and active.demo_origin_id:
            facilities = [active if f.id == active.demo_origin_id else f for f in facilities]
    return facilities


@public.get("/facilities/{facility_id}", response_model=schemas.Facility)
def get_facility(facility_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Facility, facility_id)


@router.get("/facilities/{facility_id}/latest-recommendation",
            response_model=schemas.RecommendationDetail | None)
def latest_recommendation(facility_id: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Facility, facility_id)
    from .demo import active_recommendation
    active = active_recommendation(db, facility_id)
    if active:
        return services.recommendation_detail(db, active)
    rec = db.scalar(select(models.Recommendation).where(models.Recommendation.facility_id == facility_id)
                    .where(models.Recommendation.archived_at.is_(None))
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
        rec = pipeline.evaluate_facility(db, facility, eid, target_date=body.target_date if body else None,
                                         demo_workday=bool(body and body.demo_workday))
    except ValueError as e:
        raise HTTPException(403 if "DEMO_MODE" in str(e) else 422, str(e))
    except IntegrityError:
        raise HTTPException(409, f"evaluation {eid} already exists")
    if settings.demo_mode and facility_id == "usda-kc-demo":
        from .demo import LABELS, state
        current = state(db)
        current.mode = "live_today" if body and body.demo_workday else "live_next"
        current.label = LABELS[current.mode]
        current.recommendation_id, current.active_facility_id, current.captured_at = rec.id, rec.facility_id, None
        db.commit()
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


@router.get("/recommendations/{rec_id}/map")
def recommendation_map(rec_id: str, db: Session = Depends(get_db)):
    """Facility, access segments, aggregated commute corridors (no homes) and located local facts."""
    from .mapdata import map_for

    return map_for(db, _get_or_404(db, models.Recommendation, rec_id))


@router.get("/snapshots/{digest}", include_in_schema=False)
def snapshot(digest: str, request: Request):
    """The saved copy of a source response, as plain text (never rendered as HTML)."""
    from fastapi.responses import Response, RedirectResponse
    from .snapshots import path_for
    path = path_for(digest)
    # The app-owned wrapper escapes source text. Raw bytes remain plain text for
    # API clients; no source response is ever rendered as HTML.
    if "text/html" in request.headers.get("accept", ""):
        suffix = "?role=auditor" if request.query_params.get("role") == "auditor" or request.headers.get("X-Role") == "auditor" else ""
        return RedirectResponse(f"/saved-copy/{digest}{suffix}", status_code=307)
    return Response(path.read_bytes(), media_type="text/plain; charset=utf-8",
                             headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/snapshots/{digest}/verify")
def snapshot_verify(digest: str, db: Session = Depends(get_db)):
    from .snapshots import metadata
    return metadata(db, digest)


@router.get("/record/verify")
def verify_record(facility_id: str | None = None, db: Session = Depends(get_db)):
    return record.verify(db, facility_id)


@router.post("/recommendations/{rec_id}/decision", response_model=schemas.Decision)
def decide(rec_id: str, body: schemas.DecisionIn, db: Session = Depends(get_db)):
    if _get_or_404(db, models.Recommendation, rec_id).archived_at:
        raise HTTPException(409, "This demo recommendation was reset; evaluate again")
    rec = _get_or_404(db, models.Recommendation, rec_id)
    if rec.status == "preliminary":
        raise HTTPException(409, "Preliminary - road check in progress; decide once it is final.")
    if db.scalar(select(models.Recommendation.id).where(models.Recommendation.previous_recommendation_id == rec.id).limit(1)):
        raise HTTPException(409, "This recommendation has been revised; review the latest version before deciding")
    if db.scalar(select(models.Decision).where(models.Decision.recommendation_id == rec_id)):
        raise HTTPException(409, "A decision already exists for this recommendation; re-evaluate to decide again.")
    return services.record_decision(db, rec, body)


@router.get("/decisions/{decision_id}", response_model=schemas.Decision)
def get_decision(decision_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Decision, decision_id)


@router.get("/decisions/{decision_id}/notice", response_model=schemas.NoticeDetail)
def decision_notice(decision_id: str, db: Session = Depends(get_db)):
    decision = _get_or_404(db, models.Decision, decision_id)
    notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    if notice is None:
        raise HTTPException(404, "no notice for this decision (temporary locations have no roster)")
    detail = people_api.notice_detail(db, notice)
    job = db.get(models.ScheduledJob, f"send-notice:{decision_id}")
    if job and job.status == "pending":
        detail.scheduled_send_at = job.run_at
    return detail


@router.post("/decisions/{decision_id}/notify", response_model=list[schemas.Notification])
def notify(decision_id: str, db: Session = Depends(get_db)):
    """Send the decision's notice (wave 1, wave 2, then FYI). Idempotent."""
    decision = _get_or_404(db, models.Decision, decision_id)
    notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    if notice is None:
        raise HTTPException(404, "no notice for this decision")
    try:
        return send_notice(db, notice.id)
    except NoDecisionError as e:
        raise HTTPException(404, str(e))


@router.get("/decisions/{decision_id}/notifications", response_model=list[schemas.Notification])
def notifications(decision_id: str, db: Session = Depends(get_db)):
    notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == decision_id))
    if notice is None:
        return []
    return [n for n in db.scalars(select(models.Notification).where(models.Notification.notice_id == notice.id))
            if settings.staffing_feature or n.message_type not in {"staffing_alert", "call_in"}]


@router.post("/decisions/{decision_id}/schedule-notify", status_code=202)
def schedule_notify(decision_id: str, body: dict, db: Session = Depends(get_db)):
    from .db import utcnow
    from .schemas import InvestigationIn
    decision = _get_or_404(db, models.Decision, decision_id)
    if decision.archived_at:
        raise HTTPException(409, "This decision was reset")
    if not settings.scheduler_loop:
        raise HTTPException(503, "Scheduling is disabled; send manually")
    try:
        at = InvestigationIn(action="schedule_recheck", at=body.get("at")).at
    except ValueError:
        raise HTTPException(422, "Choose a future time with a timezone")
    if at <= utcnow():
        raise HTTPException(422, "Send time must be in the future")
    from zoneinfo import ZoneInfo
    rec = db.get(models.Recommendation, decision.recommendation_id)
    facility = db.get(models.Facility, rec.facility_id)
    if not db.scalar(select(models.Notice.id).where(models.Notice.decision_id == decision_id)):
        raise HTTPException(404, "No notice for this decision")
    if at.astimezone(ZoneInfo(facility.timezone)).date() > rec.operating_date:
        raise HTTPException(422, "Send no later than the decision's target day")
    key = f"send-notice:{decision_id}"
    job = db.get(models.ScheduledJob, key)
    if job:
        raise HTTPException(409, "A send is already scheduled for this decision")
    db.add(models.ScheduledJob(key=key, facility_id=rec.facility_id, operating_date=rec.operating_date,
                              kind="notification_send", run_at=at, status="pending", result={"decision_id": decision_id}))
    record.append(db, "notification.scheduled", {"decision_id": decision_id, "at": at}, facility_id=rec.facility_id)
    db.commit()
    return {"at": at, "status": "scheduled"}


# ---------------------------------------------------------------- local signal sources

@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    """The local-signals allowlist with each source's last fetch, plus integrated and skipped sources."""
    from .sources import local_signals as ls

    latest: dict[str, models.Evidence] = {}
    for e in db.scalars(select(models.Evidence).where(models.Evidence.kind == "local_feed")
                        .order_by(models.Evidence.retrieved_at.desc()).limit(500)):
        sid = (e.value or {}).get("source_id")
        if sid and sid not in latest:
            latest[sid] = e
    rows = []
    for src in ls.sources():
        e = latest.get(src.id)
        v = e.value if e else {}
        rows.append({
            "id": src.id, "name": src.name, "url": src.url, "link": src.link, "kind": src.kind, "agent": src.agent,
            "authority": src.authority, "area": src.area, "refresh_minutes": src.refresh_minutes, "robots": src.robots,
            "coordinates": src.coordinates,
            "last_fetch": e.retrieved_at if e else None, "status": v.get("fetch_status") if e else "never fetched",
            "items_found": v.get("item_count") if e else None, "error": v.get("error") if e else None,
        })
    return {"sources": rows, "integrated": ls.registry().get("integrated", []), "skipped": ls.skipped()}


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
               limit: int | None = None, newest_first: bool = False, db: Session = Depends(get_db), v: Viewer = Depends(viewer)):
    events = record.list_events(db, facility_id, _aware(since), _aware(until), limit, newest_first)
    if v.role == "auditor":
        return [schemas.RecordEvent.model_validate(e).model_copy(update={"payload": audit_payload(e.payload)}) for e in events]
    return events


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
def record_at(facility_id: str, timestamp: datetime, db: Session = Depends(get_db), v: Viewer = Depends(viewer)):
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
            notice = db.scalar(select(models.Notice).where(models.Notice.decision_id == detail.decision.id))
            notes = db.scalars(select(models.Notification).where(
                models.Notification.notice_id == (notice.id if notice else ""),
                models.Notification.sent_at <= ts)).all() if notice else []
    if v.role == "auditor" and detail:
        detail.people = None
        detail.rationale = audit_payload(detail.rationale)
        if detail.decision:
            detail.decision.reason = audit_payload(detail.decision.reason)
        for ev in detail.evidence:
            ev.value = audit_payload(ev.value)
        notes = []  # audit memos contain aggregate notification counts, never personal messages
    return schemas.RecordAt(as_of=ts, facility_id=facility_id, recommendation=detail,
                            notifications=[schemas.Notification.model_validate(n) for n in notes])

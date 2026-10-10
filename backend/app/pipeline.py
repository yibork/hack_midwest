"""One evaluation: weather agent -> rules engine (pure, unchanged) -> writer agent -> stored
Recommendation, all under one evaluation_id.

Then: employee home-zone alerts are stored as Evidence (people exposure), and message wording is
pre-drafted in the background so approval can send at once.

If an agent is unreachable, errors, or takes longer than AGENT_TIMEOUT_SECONDS, the same tools
are called directly in code, the template rationale is used, and "agent_unavailable" is recorded.
"""
import uuid

from sqlalchemy.orm import Session

from . import models, record, schemas
from .agents import message_agent, weather_agent, writer_agent
from .agents.runtime import AgentUnavailable, new_state, record_unavailable
from .config import settings
from .db import utcnow
from .notify import exposure
from .notify.plan import load_inputs
from .rules.engine import evaluate
from .sources import nws


def evaluate_facility(db: Session, facility: models.Facility,
                      evaluation_id: str | None = None) -> models.Recommendation:
    eid = evaluation_id or uuid.uuid4().hex
    fac = schemas.Facility.model_validate(facility)
    record.append_now("evaluation.started", {"evaluation_id": eid, "facility": fac.name},
                      facility_id=fac.id, evaluation_id=eid)

    # 1. Evidence: the weather agent calls the NWS tools and annotates what they stored.
    state = new_state(evaluation_id=eid, facility_id=fac.id)
    agent_ran = False
    try:
        notes = weather_agent.run(fac, state, settings.agent_timeout)
        agent_ran = True
        weather_agent.apply_notes(notes, fac.id, eid)
    except AgentUnavailable as e:
        record_unavailable(weather_agent.NAME, str(e), "NWS tools called directly", state)
    weather_agent.ensure_required_evidence(fac, eid, agent_ran)
    evidence = weather_agent.evidence_for(eid)

    # 1b. People exposure: alerts for employees' home zones, one request per unique zone.
    if not fac.temporary:
        people = load_inputs(db, facility, utcnow().date())[0]
        if people:
            exposure.collect(fac.id, eid, people)

    # 2. Recommendation: deterministic rules engine over the stored Evidence rows.
    now = utcnow()
    rec = evaluate(fac, evidence, now)
    rec.evaluation_id = eid

    # 3. Explanation: writer agent, validated; template on any problem.
    rec.rationale, rec.rationale_source = writer_agent.write_rationale(
        rec, evidence, facility_name=fac.name, tz=fac.timezone,
        state=new_state(evaluation_id=eid, facility_id=fac.id), timeout=settings.agent_timeout)

    row = models.Recommendation(**rec.model_dump(mode="python", exclude={"triggers"}),
                                triggers=[t.model_dump() for t in rec.triggers])
    db.add(row)
    record.append(db, "recommendation.created", {
        "recommendation_id": rec.id, "evaluation_id": eid, "posture": rec.posture,
        "confidence": rec.confidence, "operating_date": rec.operating_date,
        "policy_version": rec.policy_version, "triggers": [t.rule_id for t in rec.triggers],
        "evidence_ids": rec.evidence_ids, "rationale_source": rec.rationale_source,
    }, facility_id=fac.id, evaluation_id=eid)
    db.commit()
    if not fac.temporary:
        message_agent.start_predraft(row.id)  # wording ready before the manager approves
    return row


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

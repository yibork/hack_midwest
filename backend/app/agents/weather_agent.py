"""Weather evidence agent.

The agent chooses which NWS tools to call and writes a relevance note per Evidence row. The tools
do the real work in code: fetch, snapshot, store Evidence (with evaluation_id). The rules engine
reads numbers only from those Evidence rows, never from the model's text.
"""
import json
from datetime import date, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from strands import ToolContext, tool

from .. import record, schemas
from ..db import SessionLocal, utcnow
from ..models import Evidence, Facility
from ..rules.engine import compute_windows, load_policy, peak_gust
from ..services import store_evidence_now
from ..sources import nws
from .runtime import AgentUnavailable, invoke, make_agent

NAME = "weather_agent"
REQUIRED_KINDS = ("active_alerts", "wind_forecast")

SYSTEM_PROMPT = """You are the weather evidence agent for Closure Call, a tool that helps a USDA \
facility manager decide whether to change a facility's operating posture.

Your job is evidence, not decisions:
1. Call fetch_alerts_for_facility and fetch_commute_window_forecast for the facility.
2. For every evidence_id the tools return, write one short relevance note (max 25 words) saying how \
it bears on the facility: e.g. "Alert covers Jackson County; facility is inside the area", "No \
active alerts cover the facility", "Commute-window gusts peak at 18 mph".
3. Add ambiguity_flags only for genuine uncertainty, e.g. "alert area is adjacent; relevance \
uncertain", "alert timing unclear", "source unavailable". Usually the list is empty. If \
commute_window_already_passed is true, having no commute-window gusts is expected: do not flag it.

Never recommend a posture (normal, delay, dismissal, telework, closure). Only use numbers that \
appear in tool results."""


class EvidenceNote(BaseModel):
    evidence_id: str
    relevance_note: str = Field(max_length=400)
    ambiguity_flags: list[str] = []


class WeatherNotes(BaseModel):
    notes: list[EvidenceNote]


# ---------------------------------------------------------------- plain functions (also the fallback)

def _load_facility(facility_id: str) -> schemas.Facility:
    with SessionLocal() as s:
        f = s.get(Facility, facility_id)
        if f is None:
            raise ValueError(f"unknown facility {facility_id}")
        return schemas.Facility.model_validate(f)


def _check_open(state: dict) -> None:
    if state.get("closed") is not None and state["closed"].is_set():
        raise RuntimeError("evaluation already finished; refusing late writes")


def fetch_alerts(facility: schemas.Facility, evaluation_id: str) -> dict:
    alerts, ev = nws.get_active_alerts_for_point(facility.lat, facility.lon)
    store_evidence_now([ev], facility.id, evaluation_id)
    return {
        "evidence_id": ev.id, "status": ev.status,
        "error": ev.value.get("error") if ev.status == "unknown" else None,
        "alerts": [{k: a.get(k) for k in ("event", "severity", "urgency", "certainty", "onset", "ends",
                                           "areaDesc", "headline")} for a in (alerts or [])],
    }


def fetch_forecast(facility: schemas.Facility, evaluation_id: str, now: datetime | None = None,
                   operating_date: date | None = None) -> dict:
    now = now or utcnow()
    evidence, point = nws.collect_forecast_evidence(facility.lat, facility.lon, now)
    store_evidence_now(evidence, facility.id, evaluation_id)
    w = compute_windows(facility, now, load_policy(), operating_date)
    by_kind = {e.kind: e for e in evidence}
    wind, hourly = by_kind.get("wind_forecast"), by_kind.get("hourly_forecast")
    conditions = []
    if hourly and hourly.status != "unknown":
        for p in hourly.value.get("periods", []):
            start = nws.parse_dt(p.get("start"))
            if start and w.commute_start <= start < w.commute_end:
                conditions.append({k: p.get(k) for k in ("start", "temperature", "wind_speed", "short_forecast",
                                                         "precip_pct")})
    return {
        "evidence": [{"evidence_id": e.id, "kind": e.kind, "status": e.status,
                      "error": e.value.get("error") if e.status == "unknown" else None} for e in evidence],
        "facility_local_timezone": facility.timezone,
        "nws_office": (point or {}).get("office"),
        "operating_date": w.operating_date.isoformat(),
        "commute_window": f"{w.commute_start:%H:%M}-{w.commute_end:%H:%M}",
        "commute_window_already_passed": now >= w.commute_end,
        "commute_peak_gust_mph": peak_gust(wind, w.commute_start, w.commute_end),
        "departure_window": f"{w.departure_start:%H:%M}-{w.departure_end:%H:%M}",
        "departure_peak_gust_mph": peak_gust(wind, w.departure_start, w.departure_end),
        "commute_conditions": conditions,
    }


def evidence_for(evaluation_id: str) -> list[schemas.Evidence]:
    with SessionLocal() as s:
        rows = s.scalars(select(Evidence).where(Evidence.evaluation_id == evaluation_id)
                         .order_by(Evidence.retrieved_at)).all()
        return [schemas.Evidence.model_validate(r) for r in rows]


def ensure_required_evidence(facility: schemas.Facility, evaluation_id: str, agent_ran: bool,
                             operating_date: date | None = None) -> None:
    """The engine must always get alerts and wind data. Fetch in code whatever is missing."""
    have = {e.kind for e in evidence_for(evaluation_id)}
    missing = [k for k in REQUIRED_KINDS if k not in have]
    if "active_alerts" in missing:
        fetch_alerts(facility, evaluation_id)
    if "wind_forecast" in missing:
        fetch_forecast(facility, evaluation_id, operating_date=operating_date)
    if missing and agent_ran:
        record.append_now("agent.tool_backfill", {"agent": NAME, "missing": missing,
                                                  "evaluation_id": evaluation_id},
                          facility_id=facility.id, evaluation_id=evaluation_id)


# ---------------------------------------------------------------- agent tools

@tool(context=True)
def fetch_alerts_for_facility(tool_context: ToolContext) -> str:
    """Fetch active NWS alerts covering the facility. Stores an Evidence row and returns its
    evidence_id with the normalized alerts (event, severity, urgency, certainty, onset, ends, area)."""
    state = tool_context.invocation_state
    _check_open(state)
    return json.dumps(fetch_alerts(_load_facility(state["facility_id"]), state["evaluation_id"]))


@tool(context=True)
def fetch_commute_window_forecast(tool_context: ToolContext) -> str:
    """Fetch the NWS point, hourly forecast and gridded wind/gust forecast for the facility.
    Stores Evidence rows and returns their evidence_ids plus commute- and departure-window peak gusts
    (mph) and the hourly conditions during the commute window."""
    state = tool_context.invocation_state
    _check_open(state)
    return json.dumps(fetch_forecast(_load_facility(state["facility_id"]), state["evaluation_id"],
                                     operating_date=state.get("operating_date")))


# ---------------------------------------------------------------- run

def run(facility: schemas.Facility, state: dict, timeout: float) -> WeatherNotes:
    agent = make_agent(NAME, SYSTEM_PROMPT, [fetch_alerts_for_facility, fetch_commute_window_forecast])
    prompt = ("Gather weather evidence for this facility and annotate each evidence_id.\n"
              + json.dumps(facility.model_dump(exclude={"temporary"})))
    result = invoke(agent, prompt, state, timeout, structured_output_model=WeatherNotes)
    notes = result.structured_output
    if not isinstance(notes, WeatherNotes):
        raise AgentUnavailable("weather agent returned no structured output")
    return notes


def apply_notes(notes: WeatherNotes, facility_id: str, evaluation_id: str) -> int:
    """Write the agent's relevance notes onto this evaluation's Evidence rows. Only text fields
    change; values and status (what the rules engine reads) are untouched."""
    applied = 0
    with SessionLocal() as s:
        rows = {r.id: r for r in s.scalars(select(Evidence).where(Evidence.evaluation_id == evaluation_id))}
        for n in notes.notes:
            row = rows.get(n.evidence_id)
            if row is None:
                continue  # model invented or mistyped an id; ignore
            row.relevance_note = n.relevance_note.strip()[:400]
            row.ambiguity_flags = [f.strip()[:200] for f in n.ambiguity_flags if f.strip()][:5]
            record.append(s, "agent.evidence_note", {
                "agent": NAME, "evidence_id": row.id, "kind": row.kind, "note": row.relevance_note,
                "ambiguity_flags": row.ambiguity_flags, "evaluation_id": evaluation_id,
            }, facility_id=facility_id, evaluation_id=evaluation_id)
            applied += 1
        s.commit()
    return applied

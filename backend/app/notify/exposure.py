"""Personal exposure inputs: active NWS alerts for employees' home zones and closed routes.

Network use is bounded: one /alerts/active?zone= request per unique home zone or county per
evaluation (each cached 2 minutes), never one per person. Results are stored as one Evidence row
(kind "home_zone_alerts") so the plan can be rebuilt later without the network.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import schemas
from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import Evidence
from ..services import store_evidence
from ..sources import nws
from ..routes import normalize_routes
from .impact import Exposure, ZoneAlert

KIND = "home_zone_alerts"


def home_zones(people: list[schemas.Employee]) -> list[str]:
    return sorted({z for p in people for z in (p.home_zone, p.home_county) if z})


def collect(facility_id: str, evaluation_id: str | None, people: list[schemas.Employee]) -> schemas.Evidence:
    """Fetch alerts once per unique zone and store a single Evidence row for the evaluation."""
    zones = home_zones(people)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(zones, pool.map(nws.get_active_alerts_for_zone, zones)))
    by_zone = {z: alerts for z, (alerts, ev) in results.items() if ev.status != "unknown"}
    errors = {z: ev.value.get("error") for z, (alerts, ev) in results.items() if ev.status == "unknown"}
    now = utcnow()
    ev = schemas.Evidence(
        id=uuid.uuid4().hex, source="nws", kind=KIND,
        value={"zones": {z: [{k: a.get(k) for k in ("event", "severity", "onset", "ends", "headline")}
                             for a in alerts] for z, alerts in by_zone.items()},
               "errors": errors, "zone_count": len(zones)},
        retrieved_at=now, valid_until=now + timedelta(minutes=15),
        url=f"{nws.BASE}/alerts/active?zone=<each of {len(zones)} home zones>",
        relevance_note=f"Active alerts for {len(zones)} employee home zones/counties (one request per zone).",
        status="unknown" if zones and not by_zone else "ok",
    )
    with SessionLocal() as s:
        store_evidence(s, [ev], facility_id, evaluation_id)
        s.commit()
    return ev


def _alerts_from(value: dict) -> list[ZoneAlert]:
    out = []
    for zone, alerts in (value.get("zones") or {}).items():
        for a in alerts:
            out.append(ZoneAlert(zone=zone, event=a.get("event") or "", onset=nws.parse_dt(a.get("onset")),
                                 ends=nws.parse_dt(a.get("ends"))))
    return out


def load(db: Session, facility_id: str, evaluation_id: str | None, now: datetime | None = None,
         evidence_ids: list[str] | None = None) -> Exposure:
    """Exposure as of the evaluation: its home_zone_alerts row, plus road closures (MoDOT evidence)
    and, in DEMO_MODE, simulated alerts/closures that are still valid."""
    now = now or utcnow()
    alerts: list[ZoneAlert] = []
    scope = Evidence.id.in_(evidence_ids) if evidence_ids is not None else Evidence.evaluation_id == evaluation_id
    if evaluation_id or evidence_ids is not None:
        row = db.scalar(select(Evidence).where(scope, Evidence.kind == KIND)
                        .order_by(Evidence.retrieved_at.desc()).limit(1))
        if row and isinstance(row.value, dict):
            alerts += _alerts_from(row.value)
    sources = ["modot", "demo"] if settings.demo_mode else ["modot"]
    query = select(Evidence).where(
        Evidence.facility_id == facility_id, Evidence.source.in_(sources),
        Evidence.kind.in_(["road_closure", "simulated_zone_alert"]),
        or_(Evidence.valid_until.is_(None), Evidence.valid_until > now))
    if evidence_ids is not None:
        query = query.where(scope)
    extra = db.scalars(query).all()
    routes = set()
    closed_for: dict[str, set[str]] = {}
    if evaluation_id or evidence_ids is not None:  # only this recommendation's evidence
        for e in db.scalars(select(Evidence).where(scope,
                                                   Evidence.kind == "local_fact")):
            v = e.value if isinstance(e.value, dict) else {}
            if v.get("authority") == "official_state" and v.get("fact_type") == "road_closure":
                for emp in v.get("affected_employee_ids") or []:  # coordinates only; uncertain never counts
                    closed_for.setdefault(emp, set()).update(v.get("matches_employee_commute_routes") or [])
    for e in extra:
        v = e.value if isinstance(e.value, dict) else {}
        if e.kind == "road_closure" and v.get("route"):
            routes |= normalize_routes([v["route"]])
        elif e.kind == "simulated_zone_alert" and v.get("zone"):
            alerts.append(ZoneAlert(zone=v["zone"], event=v.get("event") or "Winter Storm Warning",
                                    onset=nws.parse_dt(v.get("onset")), ends=nws.parse_dt(v.get("ends"))))
    return Exposure(alerts=tuple(alerts), closed_routes=frozenset(routes),
                    closed_for=tuple((k, tuple(sorted(v))) for k, v in sorted(closed_for.items())))

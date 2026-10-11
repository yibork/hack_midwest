"""Live weather, synthetic relocated operations. Never mutate KC or invent local coverage."""
import copy
import math
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import models, record, schemas, services
from .db import utcnow
from .rules.engine import evaluate
from .sources import local_signals as ls, nws

UNAVAILABLE = "not available for this location"


def local_feeds(facility, sources=None):
    """KC-specific adapters have no verified mapping for a relocated synthetic site."""
    return [ls.FeedResult(source=s, status="unknown", error=UNAVAILABLE + "; KC source coverage is not configured")
            for s in (ls.sources() if sources is None else sources)]


def unknown(kind, source="nws"):
    return schemas.Evidence(id=uuid.uuid4().hex, source=source, kind=kind, status="unknown",
        value={"error": UNAVAILABLE, "authority": "official_federal"}, retrieved_at=utcnow(),
        relevance_note=UNAVAILABLE + "; original KC home zones/gauge are not relocated observations.")


def clone(db, original, alert, point):
    """Preserve stable employee IDs/topics; clone only facility-specific assignments/functions."""
    routes = copy.deepcopy(original.access_routes)
    # Translate local offsets in kilometres, not raw longitude degrees at a different latitude.
    ratio = math.cos(math.radians(original.lat)) / max(0.01, math.cos(math.radians(alert.lat)))
    for segment in routes:
        if isinstance(segment, dict):
            segment["points"] = [[alert.lat + lat - original.lat,
                                  alert.lon + (lon - original.lon) * ratio] for lat, lon in segment.get("points", [])]
            segment["name"] = "Synthetic relocated " + segment.get("name", segment.get("route", "access route"))
    facility = models.Facility(id="demo-relocated-" + uuid.uuid4().hex[:12],
        name="USDA synthetic demo facility · " + (alert.area_desc or alert.event),
        address="Synthetic facility relocated to " + (alert.area_desc or alert.event),
        lat=alert.lat, lon=alert.lon, timezone=point["timeZone"], nws_office=point["office"],
        open_time=original.open_time, close_time=original.close_time, access_routes=routes,
        operating_days=original.operating_days, temporary=False, demo_origin_id=original.id)
    db.add(facility)
    db.flush()
    for assignment in db.scalars(select(models.Assignment).where(models.Assignment.facility_id == original.id)):
        db.add(models.Assignment(employee_id=assignment.employee_id, facility_id=facility.id,
            kind=assignment.kind, start_date=assignment.start_date, end_date=assignment.end_date))
    for function in db.scalars(select(models.CriticalFunction).where(models.CriticalFunction.facility_id == original.id)):
        db.add(models.CriticalFunction(facility_id=facility.id, code=function.code, name=function.name,
            min_onsite=copy.deepcopy(function.min_onsite), operating_days=function.operating_days, notes=function.notes))
    db.flush()
    return facility


def relocate(db, original, alert, scan_evidence):
    point, _ = nws.get_point(alert.lat, alert.lon)
    if not point or not point.get("timeZone") or not point.get("office"):
        raise ValueError("NWS point lookup did not confirm a timezone and office; choose another alert")
    ZoneInfo(point["timeZone"])
    facility = clone(db, original, alert, point)
    return evaluate_live(db, facility, alert, scan_evidence)


def evaluate_live(db, facility, alert=None, scan_evidence=None):
    now, eid = utcnow(), uuid.uuid4().hex
    evidence, point = nws.collect_point_evidence(facility.lat, facility.lon, now)
    if alert is not None:
        # The selected real CAP alert is itself the cited source, even if point lookup omits it.
        scan = scan_evidence[0]
        evidence += scan_evidence
        evidence.append(schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind="active_alerts",
            value={"alerts": [{"id": alert.id, "event": alert.event, "severity": alert.severity,
                "urgency": alert.urgency, "certainty": alert.certainty, "onset": alert.onset,
                "ends": alert.ends, "areaDesc": alert.area_desc, "headline": alert.headline}],
                "authority": "official_federal", "selected_live_alert": True},
            source_time=alert.sent, retrieved_at=scan.retrieved_at, valid_until=scan.valid_until,
            url=scan.url, snapshot_hash=scan.snapshot_hash, status="ok",
            relevance_note="Real NWS alert selected from the live national scan; synthetic facility at its polygon/zone centroid."))
    evidence += [unknown("home_zone_alerts"), unknown("river_categories", "nwps")]
    evidence += [feed.to_evidence() for feed in local_feeds(facility)]
    services.store_evidence(db, evidence, facility.id, eid)
    local = now.astimezone(ZoneInfo(facility.timezone))
    # Real clock and calendar windows. Pick onset's day (or today for ongoing alerts), never fake 04:00.
    day = max(local.date(), nws.parse_dt(alert.onset).astimezone(local.tzinfo).date()) if alert else local.date()
    label = "Real NWS alert, live · Synthetic facility relocated to " + facility.address.removeprefix("Synthetic facility relocated to ") + " for demonstration"
    note = f"DEMO: {day} treated as a workday at the relocated site; real current clock and facility windows. Alerts outside those windows do not trigger a posture."
    result = evaluate(schemas.Facility.model_validate(facility), evidence, now, operating_date=day,
                      target_mode="demo_workday", target_note=note)
    result.evaluation_id, result.status = eid, "final"
    result.scenario_context = {"live_relocated": True, "label": label, "alert_id": alert.id if alert else None,
                               "office": facility.nws_office}
    result.rationale = "Real NWS evidence; synthetic relocated facility. " + result.rationale
    result.template_rationale = result.rationale
    rec = models.Recommendation(**{**result.model_dump(), "triggers": [t.model_dump() for t in result.triggers]})
    db.add(rec)
    db.flush()
    record.append(db, "demo.facility_relocated" if alert else "evaluation.completed", {
        "recommendation_id": rec.id, "origin_facility_id": facility.demo_origin_id, "label": label,
        "alert_id": alert.id if alert else None, "posture": rec.posture, "operating_date": day,
        "office": facility.nws_office, "timezone": facility.timezone, "evidence_ids": rec.evidence_ids,
        "synthetic_access_routes": True, "local_coverage": UNAVAILABLE}, facility_id=facility.id, evaluation_id=eid)
    db.commit()
    return rec

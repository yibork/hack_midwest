"""NOAA-only gauge discovery and stageflow. Categories never imply facility inundation."""
import math
import re
import uuid
from datetime import timedelta

import httpx

from ..config import settings
from ..db import utcnow
from ..record import save_snapshot
from ..schemas import Evidence
from .nws import parse_dt

GAUGE_URL = "https://api.water.noaa.gov/nwps/v1/gauges/KCDM7"
BASE = "https://api.water.noaa.gov/nwps/v1/gauges"


def collect_nearby(facility, windows):
    """Nearest gauge within 40 km with defined flood categories, never a distant KC gauge."""
    from ..geo import haversine_km
    now = utcnow()
    dy = 40 / 111
    dx = dy / max(0.1, math.cos(math.radians(facility.lat)))
    params = {"bbox.xmin": facility.lon-dx, "bbox.xmax": facility.lon+dx,
              "bbox.ymin": facility.lat-dy, "bbox.ymax": facility.lat+dy, "srid": "EPSG_4326"}
    try:
        response = httpx.get(BASE, params=params, headers={"User-Agent": settings.nws_user_agent}, timeout=20, follow_redirects=False)
        response.raise_for_status()
        gauges = response.json()["gauges"]
        nearby = sorted((haversine_km((facility.lat, facility.lon), (g["latitude"], g["longitude"])), g["lid"])
                        for g in gauges if re.fullmatch(r"[A-Za-z0-9]{3,8}", g.get("lid", "")))
        for distance, lid in nearby:
            if distance > 40:
                continue
            url = BASE + "/" + lid
            detail = httpx.get(url, headers={"User-Agent": settings.nws_user_agent}, timeout=20, follow_redirects=False)
            detail.raise_for_status()
            data = detail.json()
            categories = data.get("flood", {}).get("categories", {})
            if not all(isinstance(categories.get(c, {}).get("stage"), (int, float)) and categories[c]["stage"] > -900
                       for c in ("action", "minor", "moderate", "major")):
                continue
            evidence = collect(windows, gauge=lid)
            for ev in evidence:
                ev.value.update(name=data.get("name", lid), distance_km=round(distance, 1), categories=categories)
            return evidence
        return [Evidence(id=uuid.uuid4().hex, source="nwps", kind="river_categories", status="unknown",
            value={"applicable": False, "error": "no river gauge nearby", "radius_km": 40}, retrieved_at=now,
            valid_until=now+timedelta(minutes=15), url=str(response.url), snapshot_hash=save_snapshot(response.content),
            relevance_note="No NOAA river gauge with flood categories within 40 km; not evidence of safe river conditions.")]
    except Exception as exc:
        return [Evidence(id=uuid.uuid4().hex, source="nwps", kind="river_categories", status="unknown",
            value={"error": f"Nearby NOAA gauge lookup failed ({type(exc).__name__})"}, retrieved_at=now,
            relevance_note="Applicable gauge lookup failed; river conditions are unknown.")]


def collect(windows, gauge="KCDM7"):
    if not re.fullmatch(r"[A-Za-z0-9]{3,8}", gauge):
        raise ValueError("Invalid NOAA gauge identifier")
    gauge = gauge.upper()
    gauge_url = BASE + "/" + gauge
    evidence = []
    for kind, url in (("river_categories", gauge_url), ("river_forecast", gauge_url + "/stageflow")):
        now = utcnow()
        try:
            response = httpx.get(url, headers={"User-Agent": settings.nws_user_agent}, timeout=20, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if kind == "river_categories":
                if data.get("lid", "").upper() != gauge:
                    raise ValueError("Wrong gauge")
                value = {"gauge": gauge, "name": data.get("name", gauge), "categories": data["flood"]["categories"],
                         "units": data["flood"]["stageUnits"]}
                source_time = None
            else:
                forecast = data.get("forecast") or {}
                values = [p for p in forecast.get("data", []) if parse_dt(p.get("validTime"))
                          and windows.commute_start <= parse_dt(p["validTime"]) < windows.departure_end]
                observed = (data.get("observed") or {}).get("data", [])
                last = max((p for p in observed if isinstance(p.get("primary"), (int, float)) and p["primary"] > -900),
                           key=lambda p: p.get("validTime", ""), default=None)
                value = {"gauge": gauge, "current": last, "operating_date": windows.operating_date.isoformat(),
                         "forecast": values, "units": forecast.get("primaryUnits") or (data.get("observed") or {}).get("primaryUnits")}
                source_time = parse_dt(forecast.get("issuedTime"))
                if not values and last is None:
                    value["error"] = "No river forecast samples cover the target day's commute/operating/departure windows"
                elif not values:
                    value["forecast_note"] = "No target-window forecast; stage shown is an observation, not a target-day forecast"
                    source_time = parse_dt(last.get("validTime"))
            status = "unknown" if value.get("error") else "ok"
            if status == "ok" and source_time and now - source_time > timedelta(hours=24):
                status = "stale"
            ev = Evidence(id=uuid.uuid4().hex, source="nwps", kind=kind, value=value, units=value["units"],
                          source_time=source_time, retrieved_at=now, valid_until=now + timedelta(minutes=15),
                          snapshot_hash=save_snapshot(response.content), url=url, status=status,
                          relevance_note=f"{gauge} river context for {windows.operating_date}; no automatic site-inundation inference")
        except Exception as exc:
            ev = Evidence(id=uuid.uuid4().hex, source="nwps", kind=kind, status="unknown", retrieved_at=now,
                          url=url, value={"error": f"NWPS unavailable ({type(exc).__name__})"},
                          relevance_note="Missing river data is unknown, not safe")
        evidence.append(ev)
    return evidence

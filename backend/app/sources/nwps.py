"""Fixed allowlisted KCDM7 endpoints. River categories do not imply facility inundation."""
import json
import uuid
from datetime import timedelta

import httpx

from ..config import settings
from ..db import utcnow
from ..record import save_snapshot
from ..schemas import Evidence
from .nws import parse_dt

GAUGE_URL = "https://api.water.noaa.gov/nwps/v1/gauges/KCDM7"


def collect(windows):
    evidence = []
    for kind, url in (("river_categories", GAUGE_URL), ("river_forecast", GAUGE_URL + "/stageflow")):
        now = utcnow()
        try:
            response = httpx.get(url, headers={"User-Agent": settings.nws_user_agent}, timeout=20, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if kind == "river_categories":
                if data.get("lid", "").upper() != "KCDM7":
                    raise ValueError("Wrong gauge")
                value = {"gauge": "KCDM7", "categories": data["flood"]["categories"],
                         "units": data["flood"]["stageUnits"]}
                source_time = None
            else:
                forecast = data["forecast"]
                values = [p for p in forecast.get("data", []) if parse_dt(p.get("validTime"))
                          and windows.commute_start <= parse_dt(p["validTime"]) < windows.departure_end]
                value = {"gauge": "KCDM7", "operating_date": windows.operating_date.isoformat(),
                         "forecast": values, "units": forecast["primaryUnits"]}
                source_time = parse_dt(forecast.get("issuedTime"))
                if not values:
                    value["error"] = "No river forecast samples cover the target day's commute/operating/departure windows"
            status = "unknown" if value.get("error") else "ok"
            if status == "ok" and source_time and now - source_time > timedelta(hours=24):
                status = "stale"
            ev = Evidence(id=uuid.uuid4().hex, source="nwps", kind=kind, value=value, units=value["units"],
                          source_time=source_time, retrieved_at=now, valid_until=now + timedelta(minutes=15),
                          snapshot_hash=save_snapshot(response.content), url=url, status=status,
                          relevance_note=f"KCDM7 river context for {windows.operating_date}; no automatic site-inundation inference")
        except Exception as exc:
            ev = Evidence(id=uuid.uuid4().hex, source="nwps", kind=kind, status="unknown", retrieved_at=now,
                          url=url, value={"error": f"NWPS unavailable ({type(exc).__name__})"},
                          relevance_note="Missing river data is unknown, not safe")
        evidence.append(ev)
    return evidence

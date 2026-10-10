"""National Weather Service client (https://api.weather.gov).

Every call returns (parsed_data_or_None, Evidence). The raw response is saved as a snapshot
and referenced by hash. On any failure the Evidence has status "unknown" and parsed data is
None; callers must never treat that as "safe".
"""
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from ..config import settings
from ..db import utcnow
from ..record import save_snapshot
from ..schemas import Evidence, NationalAlert

log = logging.getLogger(__name__)

BASE = "https://api.weather.gov"
CACHE_TTL_SECONDS = 120
_cache: dict[str, tuple[float, bytes, datetime]] = {}

SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}
URGENCY_RANK = {"Immediate": 0, "Expected": 1, "Future": 2, "Past": 3}
# UGC prefixes of marine zones. Alerts covering only water can't be evaluated as a facility.
MARINE_UGC = ("AMZ", "ANZ", "GMZ", "PZZ", "PKZ", "PHZ", "PMZ", "PSZ", "LCZ", "LEZ", "LHZ", "LMZ", "LOZ", "LSZ", "SLZ")


# ---------------------------------------------------------------- HTTP + evidence plumbing

def _fetch(url: str, params: dict | None = None) -> tuple[bytes, datetime]:
    """GET with a 2-minute in-memory cache. Returns (raw bytes, retrieved_at)."""
    key = str(httpx.URL(url, params=params))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1], hit[2]
    headers = {"User-Agent": settings.nws_user_agent, "Accept": "application/geo+json"}
    last_error: Exception | None = None
    for _ in range(2):  # gridpoint endpoints occasionally 500; one retry
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            retrieved = utcnow()
            _cache[key] = (time.monotonic(), resp.content, retrieved)
            return resp.content, retrieved
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code < 500:
                break
        except httpx.HTTPError as e:
            last_error = e
    raise RuntimeError(f"NWS request failed: {last_error}")


def _call(kind: str, url: str, params: dict | None, parse: Callable[[dict], tuple[Any, Any]], *,
          note: str, units: str | None = None, valid_for: timedelta = timedelta(minutes=15),
          source_time_key: str | None = None, stale_after: timedelta | None = None):
    """Fetch, snapshot, parse and wrap in Evidence. parse(data) returns (parsed, evidence_value)."""
    full_url = str(httpx.URL(url, params=params))
    try:
        raw, retrieved = _fetch(url, params)
        data = json.loads(raw)
        parsed, value = parse(data)
    except Exception as e:  # noqa: BLE001 - any failure becomes "unknown" evidence
        log.warning("NWS %s failed: %s", kind, e)
        return None, Evidence(
            id=uuid.uuid4().hex, source="nws", kind=kind, value={"error": str(e)}, units=units,
            retrieved_at=utcnow(), url=full_url, relevance_note=note, status="unknown",
        )
    source_time = None
    if source_time_key:
        source_time = parse_dt((data.get("properties") or {}).get(source_time_key) or data.get(source_time_key))
    status = "ok"
    if stale_after and source_time and retrieved - source_time > stale_after:
        status = "stale"
    return parsed, Evidence(
        id=uuid.uuid4().hex, source="nws", kind=kind, value=value, units=units,
        source_time=source_time, retrieved_at=retrieved, valid_until=retrieved + valid_for,
        url=full_url, snapshot_hash=save_snapshot(raw), relevance_note=note, status=status,
    )


def unknown_evidence(kind: str, note: str, reason: str) -> Evidence:
    return Evidence(id=uuid.uuid4().hex, source="nws", kind=kind, value={"error": reason},
                    retrieved_at=utcnow(), relevance_note=note, status="unknown")


# ---------------------------------------------------------------- parsing helpers

def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_duration(value: str) -> timedelta:
    m = _DURATION.match(value)
    if not m:
        raise ValueError(f"bad ISO-8601 duration {value!r}")
    d, h, mins = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mins)


def _to_mph_factor(uom: str | None) -> float:
    uom = uom or ""
    if uom.endswith("km_h-1"):
        return 0.621371
    if uom.endswith("m_s-1"):
        return 2.23694
    if uom.endswith("kn"):
        return 1.15078
    return 1.0


def parse_alert(feature: dict) -> dict:
    p = feature.get("properties", {})
    return {
        "id": p.get("id") or feature.get("id"),
        "event": p.get("event"),
        "severity": p.get("severity") or "Unknown",
        "certainty": p.get("certainty") or "Unknown",
        "urgency": p.get("urgency") or "Unknown",
        "onset": p.get("onset") or p.get("effective"),
        "ends": p.get("ends") or p.get("expires"),
        "headline": p.get("headline"),
        "instruction": p.get("instruction"),
        "areaDesc": p.get("areaDesc"),
        "sent": p.get("sent"),
    }


def _centroid(geometry: dict | None) -> tuple[float, float] | None:
    """Rough centroid: mean of outer-ring vertices. Good enough to pick a point to evaluate."""
    if not geometry:
        return None
    gtype, coords = geometry.get("type"), geometry.get("coordinates")
    if gtype == "Polygon":
        rings = [coords[0]]
    elif gtype == "MultiPolygon":
        rings = [poly[0] for poly in coords]
    elif gtype == "Point":
        return coords[1], coords[0]
    else:
        return None
    pts = [pt for ring in rings for pt in ring]
    if not pts:
        return None
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


# ---------------------------------------------------------------- public API

def get_point(lat: float, lon: float):
    """-> ({forecast, forecastHourly, forecastGridData, timeZone, ...}, Evidence)"""
    def parse(data):
        p = data["properties"]
        info = {
            "forecast": p["forecast"],
            "forecastHourly": p["forecastHourly"],
            "forecastGridData": p["forecastGridData"],
            "timeZone": p.get("timeZone"),
            "office": p.get("gridId"),
            "city": (p.get("relativeLocation") or {}).get("properties", {}).get("city"),
            "state": (p.get("relativeLocation") or {}).get("properties", {}).get("state"),
            "forecastZone": (p.get("forecastZone") or "").rsplit("/", 1)[-1] or None,
            "county": (p.get("county") or "").rsplit("/", 1)[-1] or None,
        }
        return info, info

    return _call("point", f"{BASE}/points/{lat:.4f},{lon:.4f}", None, parse,
                 note="Maps the facility location to its NWS forecast office and grid.",
                 valid_for=timedelta(days=1))


def get_active_alerts_for_point(lat: float, lon: float):
    """-> (list of parsed alerts, Evidence)"""
    def parse(data):
        alerts = [parse_alert(f) for f in data.get("features", [])]
        return alerts, {"count": len(alerts), "alerts": alerts}

    return _call("active_alerts", f"{BASE}/alerts/active", {"point": f"{lat:.4f},{lon:.4f}"}, parse,
                 note="Active NWS watches, warnings and advisories whose area covers the facility.",
                 source_time_key="updated", valid_for=timedelta(minutes=15))


def get_active_alerts_for_zone(zone: str):
    """-> (list of parsed alerts, Evidence) for one NWS forecast zone or county code (e.g. MOZ037,
    MOC095). Cached 2 minutes like every NWS call; callers query each unique zone once."""
    def parse(data):
        alerts = [parse_alert(f) for f in data.get("features", [])]
        return alerts, {"zone": zone, "count": len(alerts), "alerts": alerts}

    return _call("zone_alerts", f"{BASE}/alerts/active", {"zone": zone}, parse,
                 note=f"Active NWS alerts for zone {zone} (employee home areas).",
                 source_time_key="updated", valid_for=timedelta(minutes=15))


def get_hourly_forecast(url: str):
    """-> (all hourly periods, ~7 days, Evidence). Context for the rationale; no gusts here.
    Callers check that the target day's windows are covered (hourly_coverage)."""
    def parse(data):
        periods = [{
            "start": p.get("startTime"),
            "end": p.get("endTime"),
            "temperature": p.get("temperature"),
            "temperature_unit": p.get("temperatureUnit"),
            "wind_speed": p.get("windSpeed"),
            "wind_direction": p.get("windDirection"),
            "short_forecast": p.get("shortForecast"),
            "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
        } for p in data["properties"]["periods"]]
        return periods, {"periods": periods}

    return _call("hourly_forecast", url, None, parse,
                 note="Hourly forecast for the facility grid (temperature, wind, conditions).",
                 source_time_key="updateTime", valid_for=timedelta(hours=3),
                 stale_after=timedelta(hours=12))


GRID_HOURS = 168  # the grid forecast covers ~7 days; evaluations may target a later operating day


def periods_in(periods: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Hourly periods overlapping [start, end). startTime/endTime carry their UTC offsets, so this
    compares absolute instants (no local-date guessing)."""
    out = []
    for p in periods:
        s, e = parse_dt(p.get("start")), parse_dt(p.get("end"))
        if s is None:
            continue
        e = e or s + timedelta(hours=1)
        if s < end and e > start:
            out.append(p)
    return out


def hourly_coverage(ev: Evidence, windows: dict[str, tuple[datetime, datetime]]) -> list[str]:
    """Mark hourly evidence "unknown" if any window has no periods: missing data must not read as
    calm. Returns the names of the uncovered windows."""
    if ev.status == "unknown" or not isinstance(ev.value, dict):
        return list(windows)
    missing = []
    for name, (start, end) in windows.items():
        if not periods_in(ev.value.get("periods", []), start, end):
            tz = start.tzinfo
            missing.append(f"{name} window ({start.astimezone(tz):%a %b %d %H:%M}-{end.astimezone(tz):%H:%M} local)")
    if missing:
        ev.status = "unknown"
        ev.value = {**ev.value, "error": "no hourly periods found for " + " and ".join(missing)}
    return missing


def get_grid_data(url: str, now: datetime | None = None):
    """-> (hourly wind speed and gust in mph for the next 7 days, Evidence).
    peak_gust_mph is the peak of the next 24 hours."""
    now = now or utcnow()
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=GRID_HOURS)

    def expand(series: dict) -> dict[datetime, float]:
        factor = _to_mph_factor(series.get("uom"))
        out: dict[datetime, float] = {}
        for v in series.get("values", []):
            if v.get("value") is None:
                continue
            t0_s, dur_s = v["validTime"].split("/")
            t0, dur = parse_dt(t0_s), parse_duration(dur_s)
            t = t0
            while t < t0 + dur:
                if start <= t < end:
                    out[t] = round(v["value"] * factor, 1)
                t += timedelta(hours=1)
        return out

    def parse(data):
        p = data["properties"]
        speed, gust = expand(p.get("windSpeed", {})), expand(p.get("windGust", {}))
        hours = [{"time": t.isoformat(), "wind_mph": speed.get(t), "gust_mph": gust.get(t)}
                 for t in sorted(set(speed) | set(gust))]
        day1 = (start + timedelta(hours=24)).isoformat()
        gusts = [h["gust_mph"] for h in hours if h["gust_mph"] is not None and h["time"] < day1]
        value = {"hours": hours, "peak_gust_mph": max(gusts) if gusts else None}
        return value, value

    return _call("wind_forecast", url, None, parse, units="mph",
                 note="Forecast sustained wind and gusts for the next 7 days at the facility grid.",
                 source_time_key="updateTime", valid_for=timedelta(hours=3),
                 stale_after=timedelta(hours=12))


def collect_forecast_evidence(lat: float, lon: float, now: datetime | None = None):
    """Point lookup + hourly forecast + grid winds. -> (evidence list, point info or None)"""
    point, ev_point = get_point(lat, lon)
    evidence = [ev_point]
    if point:
        evidence.append(get_hourly_forecast(point["forecastHourly"])[1])
        evidence.append(get_grid_data(point["forecastGridData"], now)[1])
    else:
        reason = "point lookup failed, so the forecast grid is unknown"
        evidence.append(unknown_evidence("hourly_forecast", "Hourly forecast for the facility grid.", reason))
        evidence.append(unknown_evidence("wind_forecast", "Forecast wind and gusts at the facility grid.", reason))
    return evidence, point


def collect_point_evidence(lat: float, lon: float, now: datetime | None = None):
    """Everything the rules engine needs for one location. -> (evidence list, point info or None)"""
    evidence, point = collect_forecast_evidence(lat, lon, now)
    evidence.insert(1, get_active_alerts_for_point(lat, lon)[1])
    return evidence, point


def get_active_alerts_national(limit: int = 20):
    """-> (top `limit` NationalAlerts by severity/urgency, list of Evidence)"""
    def parse(data):
        alerts = []
        for f in data.get("features", []):
            ugc = (f.get("properties", {}).get("geocode") or {}).get("UGC") or []
            if ugc and all(z.startswith(MARINE_UGC) for z in ugc):
                continue
            a = parse_alert(f)
            c = _centroid(f.get("geometry"))
            zones = f.get("properties", {}).get("affectedZones") or []
            alerts.append((a, c, zones[0] if zones else None))
        alerts.sort(key=lambda x: (SEVERITY_RANK.get(x[0]["severity"], 9),
                                   URGENCY_RANK.get(x[0]["urgency"], 9),
                                   x[1] is None))
        top = alerts[:limit]
        return top, {"count": len(alerts), "top_ids": [a["id"] for a, _, _ in top]}

    top, ev = _call("national_alerts", f"{BASE}/alerts/active", {"status": "actual"}, parse,
                    note="All active US alerts; used to pick places worth evaluating.",
                    source_time_key="updated", valid_for=timedelta(minutes=15))
    evidence = [ev]
    if top is None:
        return [], evidence

    # Zone-based alerts have no geometry; look up the first affected zone's shape instead.
    def zone_centroid(zone_url):
        _, zev = _call("zone_geometry", zone_url, None, lambda d: (d, {"id": d.get("id")}),
                       note="Zone shape used to place a zone-based alert on the map.",
                       valid_for=timedelta(days=1))
        if zev.status == "unknown":
            return None, zev
        raw, _ = _fetch(zone_url)  # cached by the call above
        return _centroid(json.loads(raw).get("geometry")), zev

    missing = [i for i, (_, c, z) in enumerate(top) if c is None and z]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (c, zev) in zip(missing, pool.map(lambda i: zone_centroid(top[i][2]), missing)):
            evidence.append(zev)
            top[i] = (top[i][0], c, top[i][2])

    results = [NationalAlert(
        id=a["id"], event=a["event"], severity=a["severity"], certainty=a["certainty"],
        urgency=a["urgency"], onset=a["onset"], ends=a["ends"], headline=a["headline"],
        area_desc=a["areaDesc"], sent=a["sent"],
        lat=round(c[0], 4) if c else None, lon=round(c[1], 4) if c else None,
    ) for a, c, _ in top]
    return results, evidence

"""Nationwide supplementary AirNow signal. Fixed HTTPS endpoints; no credential logging.

urllib deliberately avoids httpx's automatic request-URL logging (AirNow requires a
query-string key). Redirects are prohibited. Only sanitized bytes reach saved copies.
Daily forecast issue dates have no hour: their two-hour freshness is retrieval-based;
observation freshness is based on the provider's observation hour, not the fetch time.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from ..config import redact, settings
from ..db import utcnow
from ..record import save_snapshot
from ..schemas import Evidence

BASE = "https://www.airnowapi.org"
ENDPOINTS = {"air_quality_current": BASE + "/aq/observation/current/ziplatlong/",
             "air_quality_forecast": BASE + "/aq/forecast/current/"}
FRESHNESS = timedelta(hours=2)
POLLUTANTS = {"PM2.5": "PM2.5", "PM25": "PM2.5", "O3": "ozone", "OZONE": "ozone", "PM10": "PM10"}
OFFSETS = {"UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
           "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10, "AST": -4}
CATEGORIES = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for Sensitive Groups"),
              (200, "Unhealthy"), (300, "Very Unhealthy"), (float("inf"), "Hazardous")]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("AirNow redirect blocked")


def _fetch(url):
    # No user-defined URL can enter this function through collect(). No request/exception
    # object is logged or persisted: both can contain the credential-bearing wire URL.
    with build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": settings.nws_user_agent}), timeout=20) as response:
        return response.read()


def safe_text(text):
    key = settings.airnow_api_key
    if key:
        for form in {key, quote(key, safe=""), json.dumps(key)[1:-1]}:
            text = text.replace(form, "[REDACTED]")
    return redact(text)


def _sanitize(value):
    if isinstance(value, dict):
        return {safe_text(str(k)): _sanitize(v) for k, v in value.items()
                if str(k).lower().replace("_", "") not in {"apikey", "authorization"}}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return safe_text(value) if isinstance(value, str) else value


def observation_time(row, facility_tz):
    zone = str(row.get("localTimeZone") or "").strip()
    tz = timezone(timedelta(hours=OFFSETS[zone])) if zone in OFFSETS else ZoneInfo(zone or facility_tz)
    day = date.fromisoformat(str(row["dateObserved"]).strip()[:10])
    hour = int(row["hourObserved"])
    return datetime(day.year, day.month, day.day, hour, tzinfo=tz).astimezone(timezone.utc)


def collect(facility, target_day, *, now=None):
    now = now or utcnow()
    result = []
    for kind, endpoint in ENDPOINTS.items():
        params = {"format": "application/json", "latitude": facility.lat, "longitude": facility.lon, "distance": 25}
        public_url = endpoint + "?" + urlencode(params)
        ev = Evidence(id=uuid.uuid4().hex, source="airnow", kind=kind, units="AQI", status="unknown",
            retrieved_at=now, valid_until=now + FRESHNESS, url=public_url,
            value={"authority": "official_federal", "target_date": str(target_day), "pollutants": [], "distance_miles": 25},
            relevance_note="EPA AirNow preliminary supplementary data, not regulatory monitoring; manager approval required.")
        try:
            if not settings.airnow_api_key:
                raise ValueError("AirNow key not configured")
            raw = _fetch(endpoint + "?" + urlencode({**params, "API_KEY": settings.airnow_api_key}))
            # Remove credentials even if a provider echoes them into an error/discussion.
            sanitized = _sanitize(json.loads(raw))
            if not isinstance(sanitized, list):
                raise ValueError("Unexpected AirNow response")
            safe_bytes = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()
            ev.snapshot_hash = save_snapshot(safe_bytes)
            ev.saved_copy_hash, ev.saved_copy_kind = ev.snapshot_hash, "normalized"
            ev.value["saved_copy_note"] = "Provider JSON normalized and credential fields removed before hashing"
            rows = []
            for row in sanitized:
                if not isinstance(row, dict):
                    continue
                pollutant = POLLUTANTS.get(str(row.get("parameterName", "")).upper().replace(" ", ""))
                if not pollutant:
                    continue
                forecast = kind == "air_quality_forecast"
                valid_day = str(row.get("dateValid", "")).strip()[:10] if forecast else None
                if forecast and valid_day != str(target_day):
                    continue
                aqi = row.get("aqi") if forecast else row.get("nowcastAQI")
                if isinstance(aqi, bool) or not isinstance(aqi, (int, float)) or aqi < 0 or int(aqi) != aqi:
                    continue  # category-only or unavailable AQI is unknown, never zero/good
                at = None if forecast else observation_time(row, facility.timezone)
                category = next(name for limit, name in CATEGORIES if aqi <= limit)
                rows.append({"pollutant": pollutant, "aqi": int(aqi), "category": category,
                    "provider_category": row.get("categoryName") if forecast else row.get("aqiCategoryName"),
                    "reporting_area": row.get("reportingArea") if forecast else row.get("reportingAreaName"),
                    "observation_time": at.isoformat() if at else None, "forecast_date": valid_day,
                    "issue_date": row.get("dateIssue") if forecast else None,
                    "status": "stale" if at and (now - at > FRESHNESS or at > now + timedelta(minutes=5)) else "ok"})
            ev.value["pollutants"] = rows
            if not rows:
                ev.value["error"] = "No numeric pollutant AQI available within 25 miles" + (f" for {target_day}" if kind.endswith("forecast") else "")
            else:
                ev.status = "ok" if any(r["status"] == "ok" for r in rows) else "stale"
                times = [datetime.fromisoformat(r["observation_time"]) for r in rows if r["observation_time"]]
                ev.source_time = max(times) if times else None
                if times:
                    ev.valid_until = min(now + FRESHNESS, max(times) + FRESHNESS)
        except Exception as exc:
            # Never stringify an exception or an HTTP error body containing request details.
            code = exc.code if isinstance(exc, HTTPError) else None
            ev.value["error"] = ("AirNow key not configured" if not settings.airnow_api_key else
                                 f"AirNow unavailable ({type(exc).__name__}" + (f", HTTP {code}" if code else "") + ")")
        result.append(ev)
    return result


def candidates(evidence, target_day, now, facility_tz):
    """Highest *fresh*, applicable AQI; current readings never stand in for tomorrow.

    Each pollutant is aged separately so one fresh ozone row cannot hide stale PM2.5.
    """
    latest = {}
    for ev in sorted(evidence, key=lambda e: e.retrieved_at):
        if ev.kind in ENDPOINTS:
            latest[ev.kind] = ev
    out = []
    for kind, ev in latest.items():
        if ev.status != "ok" or now - ev.retrieved_at > FRESHNESS or (ev.valid_until and ev.valid_until < now):
            continue
        for row in ev.value.get("pollutants", []):
            if row.get("status") != "ok":
                continue
            if kind == "air_quality_forecast":
                if row.get("forecast_date") != str(target_day):
                    continue
            else:
                at = datetime.fromisoformat(row["observation_time"])
                if now - at > FRESHNESS or at > now or at.astimezone(ZoneInfo(facility_tz)).date() != target_day:
                    continue
            out.append((row["aqi"], ev, row))
    return sorted(out, key=lambda r: r[0], reverse=True)

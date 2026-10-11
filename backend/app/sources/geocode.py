"""Geocode "route + cross street" text when a source has no coordinates. OpenStreetMap Nominatim,
following its usage policy (https://operations.osmfoundation.org/policies/nominatim/): descriptive
User-Agent, at most 1 request per second, every result cached (geocode_cache table), no bulk use.

Geocoded locations are approximate: they can mark commute relevance, but never trigger
access_route_closed (only source coordinates can).
"""
import logging
import re
import threading
import time

import httpx

from .. import models
from ..config import settings
from ..db import SessionLocal, utcnow
from ..routes import routes_in_text

log = logging.getLogger(__name__)
URL = "https://nominatim.openstreetmap.org/search"
VIEWBOX = "-95.40,39.50,-94.10,38.40"  # Kansas City metro (lon/lat: west, north, east, south)
_lock = threading.Lock()
_last = 0.0

_STREET = r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Pkwy|Parkway|Trfwy|Trafficway|Ln|Lane|Way|Pl|Ter|Terr)"
CROSS = re.compile(rf"\b(?:at|near|past|before|after|and|&|/)\s+((?:[NSEW]\.?\s+)?[A-Z0-9][\w.']*(?:\s+[A-Z0-9][\w.']*){{0,3}}?\s+{_STREET})\b",
                   re.I)


def query_for(text: str) -> str | None:
    """'I-35 closed at W 12th St' -> 'W 12th St, Kansas City' (route kept in the note, not the
    query: Nominatim does not resolve highway intersections reliably)."""
    if not routes_in_text(text):
        return None
    m = CROSS.search(text)
    return f"{m.group(1).strip()}, Kansas City" if m else None


def geocode(query: str) -> dict | None:
    """-> {"lat", "lon", "display_name"} or None. Cached; rate-limited to 1 request/second."""
    with SessionLocal() as s:
        hit = s.get(models.GeocodeCache, query)
        if hit is not None:
            return None if hit.lat is None else {"lat": hit.lat, "lon": hit.lon, "display_name": hit.display_name}
    if not settings.geocode:
        return None
    global _last
    with _lock:
        wait = 1.05 - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        from .local_signals import user_agent
        try:
            r = httpx.get(URL, params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us",
                                       "viewbox": VIEWBOX, "bounded": 1},
                          headers={"User-Agent": user_agent()}, timeout=10)
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("geocode failed for %r: %s", query, type(e).__name__)
            return None  # not cached: try again next time
    row = rows[0] if rows else None
    result = {"lat": round(float(row["lat"]), 5), "lon": round(float(row["lon"]), 5),
              "display_name": row.get("display_name", "")[:200]} if row else None
    with SessionLocal() as s:
        if s.get(models.GeocodeCache, query) is None:
            s.add(models.GeocodeCache(query=query, lat=result and result["lat"], lon=result and result["lon"],
                                      display_name=result and result["display_name"], provider="nominatim",
                                      created_at=utcnow()))
            s.commit()
    return result

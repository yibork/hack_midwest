"""Road facts cache: unchanged road sources -> reuse the validated facts, no model call.

Key: facility + target day + facility/corridor geometry + each road source's content hash
(parsed, area-filtered items; see FeedResult.content_hash). The background watcher (watch_tick)
refreshes the cache when a road source changes, at most one roads-agent run per 10 minutes.
"""
import hashlib
import json
import threading
import time
from datetime import date, datetime

from sqlalchemy import select

from ... import models, record
from ...db import SessionLocal, utcnow

MIN_BACKGROUND_INTERVAL_S = 600  # max 1 background roads run per 10 min
_last_background = 0.0
_lock = threading.Lock()


def geometry_signature(geo) -> str:
    return hashlib.sha256(json.dumps([[s.route, s.points, s.buffer_km, s.ramps] for s in geo.segments] +
                                     [[c.employee_id, sorted(c.routes), c.home] for c in geo.corridors],
                                     default=str).encode()).hexdigest()[:16]


def cache_key(facility_id: str, day: date, geo_sig: str, source_hashes: dict[str, str | None]) -> str | None:
    if not source_hashes or any(h is None for h in source_hashes.values()):
        return None  # a road source is unavailable: nothing to compare against
    raw = json.dumps([facility_id, day.isoformat(), geo_sig, sorted(source_hashes.items())])
    return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str | None) -> models.RoadFactsCache | None:
    if key is None:
        return None
    with SessionLocal() as s:
        row = s.get(models.RoadFactsCache, key)
        if row is not None:
            s.expunge(row)
        return row


def put(key: str | None, facility_id: str, day: date, source_hashes: dict, entries: list[dict], rejected: list[dict],
        origin: str, evaluation_id: str | None = None) -> None:
    if key is None:
        return
    with SessionLocal() as s:
        if s.get(models.RoadFactsCache, key) is None:
            s.add(models.RoadFactsCache(key=key, facility_id=facility_id, operating_date=day,
                                        source_hashes=source_hashes, entries=entries, rejected=rejected,
                                        origin=origin, evaluation_id=evaluation_id, created_at=utcnow()))
            s.commit()


def latest(facility_id: str, day: date) -> models.RoadFactsCache | None:
    with SessionLocal() as s:
        row = s.scalar(select(models.RoadFactsCache).where(models.RoadFactsCache.facility_id == facility_id,
                                                           models.RoadFactsCache.operating_date == day)
                       .order_by(models.RoadFactsCache.created_at.desc()).limit(1))
        if row is not None:
            s.expunge(row)
        return row


def background_allowed(now_monotonic: float | None = None) -> bool:
    """At most one background roads-agent run per 10 minutes (shares the model rate limiter)."""
    global _last_background
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    with _lock:
        if _last_background and now_monotonic - _last_background < MIN_BACKGROUND_INTERVAL_S:
            return False
        _last_background = now_monotonic
        return True


def reset_rate_limit() -> None:
    global _last_background
    _last_background = 0.0


def watch_tick(now: datetime | None = None) -> list[dict]:
    """Background watcher: for each facility's next operating day, fetch the road sources; if their
    content changed (no cache entry), run the roads agent now and cache the facts."""
    from ... import pipeline, schemas
    from ...notify.plan import load_inputs
    from .run import refresh_roads

    now = now or utcnow()
    out = []
    with SessionLocal() as db:
        facilities = db.scalars(select(models.Facility).where(models.Facility.temporary.is_(False), models.Facility.demo_origin_id.is_(None))).all()
        for f in facilities:
            fac = schemas.Facility.model_validate(f)
            day, _, _ = pipeline.choose_target(fac, now)
            people = load_inputs(db, f, day)[0]
            if people:
                out.append(refresh_roads(fac, day, people))
    return out


def summary(row: models.RoadFactsCache) -> str:
    when = row.created_at.astimezone().strftime("%Y-%m-%d %H:%M %Z") if row.created_at else "?"
    return f"roads facts reused from {when} (sources unchanged)"


__all__ = ["cache_key", "get", "put", "latest", "geometry_signature", "background_allowed", "watch_tick", "record"]

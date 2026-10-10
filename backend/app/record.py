"""Append-only decision record. Every evidence fetch, evaluation, decision and notification
writes one RecordEvent through append(). Nothing here updates or deletes rows."""
import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import redact, settings
from .db import SessionLocal, utcnow
from .models import RecordEvent


def append(db: Session, event_type: str, payload: dict[str, Any], facility_id: str | None = None,
           timestamp: datetime | None = None, evaluation_id: str | None = None) -> RecordEvent:
    ev = RecordEvent(
        timestamp=timestamp or utcnow(),
        facility_id=facility_id,
        evaluation_id=evaluation_id,
        event_type=event_type,
        # JSON-safe, with any secret that slipped into an error message removed.
        payload=json.loads(redact(json.dumps(payload, default=str))),
    )
    db.add(ev)
    return ev


def append_now(event_type: str, payload: dict[str, Any], facility_id: str | None = None,
               evaluation_id: str | None = None) -> None:
    """Append and commit in a separate session. Used by agent hooks and tools, which run in
    worker threads, so events show up in the activity feed while the agent is still running."""
    with SessionLocal() as s:
        append(s, event_type, payload, facility_id=facility_id, evaluation_id=evaluation_id)
        s.commit()


def list_events(db: Session, facility_id: str | None = None, since: datetime | None = None,
                until: datetime | None = None, limit: int | None = None, newest_first: bool = False):
    q = select(RecordEvent)
    if facility_id:
        q = q.where(RecordEvent.facility_id == facility_id)
    if since:
        q = q.where(RecordEvent.timestamp >= since)
    if until:
        q = q.where(RecordEvent.timestamp <= until)
    order = (RecordEvent.timestamp.desc(), RecordEvent.id.desc()) if newest_first else (
        RecordEvent.timestamp, RecordEvent.id)
    q = q.order_by(*order)
    if limit:
        q = q.limit(limit)
    return list(db.scalars(q))


def save_snapshot(raw: bytes) -> str:
    """Store a raw source response at snapshots/{sha256}.json and return the hash.
    Later this moves to object storage; Evidence rows only reference the hash."""
    digest = hashlib.sha256(raw).hexdigest()
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = settings.snapshot_dir / f"{digest}.json"
    if not path.exists():
        path.write_bytes(raw)
    return digest

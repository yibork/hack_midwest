"""Append-only decision record. Every evidence fetch, evaluation, decision and notification
writes one RecordEvent through append(). Nothing here updates or deletes rows."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from .config import redact, settings
from .db import SessionLocal, utcnow
from .models import RecordEvent, RecordChainHead

GENESIS = "0" * 64


def chain_key(facility_id):
    return "system:" if facility_id is None else "facility:" + facility_id


def lock_head(conn, facility_id):
    """Serialize writers *before* allocating an event ID (also works across processes).
    SQLite upsert obtains its write lock; PostgreSQL locks the facility's head row."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    table = RecordChainHead.__table__
    insert = sqlite_insert if conn.dialect.name == "sqlite" else pg_insert
    key = chain_key(facility_id)
    conn.execute(insert(table).values(key=key).on_conflict_do_nothing(index_elements=[table.c.key]))
    return conn.execute(select(table).where(table.c.key == key).with_for_update()).mappings().one()


def event_hash(prev_hash, event):
    """Versioned canonical envelope: UTC microseconds, sorted keys, compact UTF-8 JSON.
    IDs, routing, timestamps and the complete redacted payload are covered."""
    timestamp = event["timestamp"]
    envelope = {k: event[k] for k in ("id", "facility_id", "evaluation_id", "event_type", "payload")}
    envelope["timestamp"] = timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def seal_insert(conn, event):
    head = lock_head(conn, event.facility_id)
    prev = head["hash"] or GENESIS
    envelope = {k: getattr(event, k) for k in ("id", "timestamp", "facility_id", "evaluation_id", "event_type", "payload")}
    digest = event_hash(prev, envelope)
    # Only initialize the two NEW integrity columns; immutable content is never changed.
    conn.execute(update(RecordEvent.__table__).where(RecordEvent.id == event.id).values(prev_hash=prev, hash=digest))
    conn.execute(update(RecordChainHead.__table__).where(RecordChainHead.key == head["key"]).values(hash=digest, event_id=event.id))
    set_committed_value(event, "prev_hash", prev)
    set_committed_value(event, "hash", digest)


def backfill_chain(conn):
    """One-time additive sealing. Never repair already-sealed events or modify payloads.
    Pre-migration history is attested from migration time, not retroactively authenticated."""
    table = RecordEvent.__table__
    previous, tails = {}, {}
    for ev in conn.execute(select(table).order_by(table.c.id)).mappings():
        key = chain_key(ev["facility_id"])
        prev = previous.get(key, GENESIS)
        digest = ev["hash"]
        if ev["hash"] is None and ev["prev_hash"] is None:
            digest = event_hash(prev, ev)
            conn.execute(update(table).where(table.c.id == ev["id"]).values(prev_hash=prev, hash=digest))
        previous[key] = digest
        tails[key] = (ev["facility_id"], ev["id"], digest)
    for key, (facility_id, event_id, digest) in tails.items():
        head = lock_head(conn, facility_id)
        # A populated head is an integrity checkpoint; never rewrite it on restart.
        if head["event_id"] is None and head["hash"] is None:
            conn.execute(update(RecordChainHead.__table__).where(RecordChainHead.key == key).values(hash=digest, event_id=event_id))


def verify(db, facility_id, through_id=None):
    table = RecordEvent.__table__
    heads = RecordChainHead.__table__
    # Read events and their checkpoint together: a concurrent append cannot look
    # like a broken tail between two separate SELECTs.
    q = select(table, heads.c.hash.label("checkpoint_hash"), heads.c.event_id.label("checkpoint_id")).select_from(
        table.outerjoin(heads, heads.c.key == chain_key(facility_id))
    ).where(table.c.facility_id == facility_id).order_by(table.c.id)
    if through_id is not None:
        q = q.where(table.c.id <= through_id)
    prev, last, count, checkpoint = GENESIS, None, 0, None
    for ev in db.execute(q).mappings():
        checkpoint = (ev["checkpoint_hash"], ev["checkpoint_id"])
        count += 1
        reason = None
        if ev["prev_hash"] != prev:
            reason = "Previous hash does not match"
        elif not ev["hash"] or ev["hash"] != event_hash(prev, ev):
            reason = "Event content hash does not match"
        if reason:
            return {"ok": False, "event_count": count, "first_broken_event_id": ev["id"], "reason": reason,
                    "head_hash": prev, "head_event_id": last}
        prev, last = ev["hash"], ev["id"]
    if through_id is None and not count:
        head = db.get(RecordChainHead, chain_key(facility_id))
        if head and head.event_id is not None:
            if db.scalar(select(table.c.id).where(table.c.facility_id == facility_id).limit(1)):
                return verify(db, facility_id)
            checkpoint = (head.hash, head.event_id)
    if through_id is None and checkpoint and checkpoint != (prev if last else None, last):
        return {"ok": False, "event_count": count, "first_broken_event_id": checkpoint[1],
                "reason": "Chain tail does not match its checkpoint", "head_hash": prev, "head_event_id": last}
    return {"ok": True, "event_count": count, "head_hash": prev, "head_event_id": last,
            "first_broken_event_id": None, "reason": None}


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

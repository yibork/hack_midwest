"""Read-only saved copies. Source bytes are always text, never executable HTML."""
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import select

from . import models, record


def path_for(digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(404, "not found")
    path = record.settings.snapshot_dir / f"{digest}.json"
    if not path.is_file():
        raise HTTPException(404, "snapshot not found")
    return path


def ensure_copy(evidence):
    """Add provenance without inventing missing raw responses or changing old values."""
    if evidence.saved_copy_hash:
        return
    raw = evidence.snapshot_hash
    if not raw and isinstance(evidence.value, dict):
        raw = evidence.value.get("snapshot_hash")
    if raw and re.fullmatch(r"[0-9a-f]{64}", raw):
        evidence.saved_copy_hash, evidence.saved_copy_kind = raw, "raw"
    else:
        envelope = {"copy_kind": "normalized", "note": "Saved normalized evidence; original raw response was not stored.",
                    "source_url": evidence.url, "retrieved_at": evidence.retrieved_at.isoformat(),
                    "source_time": evidence.source_time.isoformat() if evidence.source_time else None,
                    "source": evidence.source, "kind": evidence.kind, "scenario_kind": evidence.scenario_kind,
                    "status": evidence.status, "value": evidence.value, "relevance_note": evidence.relevance_note}
        # Normalized internal data must never expose employee home coordinates.
        def safe(value):
            if isinstance(value, dict):
                return {k: safe(v) for k, v in value.items() if k not in {"home_lat", "home_lon", "home_coordinates", "accommodation_needs"}}
            if isinstance(value, list):
                return [safe(v) for v in value]
            return value
        evidence.saved_copy_hash = record.save_snapshot(json.dumps(safe(envelope), sort_keys=True, default=str).encode())
        evidence.saved_copy_kind = "normalized"


def backfill(db):
    added = {}
    for ev in db.scalars(select(models.Evidence).where(models.Evidence.saved_copy_hash.is_(None))):
        ensure_copy(ev)
        added.setdefault(ev.facility_id, []).append({"evidence_id": ev.id, "sha256": ev.saved_copy_hash, "copy_kind": ev.saved_copy_kind})
    for facility_id, copies in sorted(added.items(), key=lambda pair: pair[0] or ""):
        record.append(db, "saved_copies.backfilled", {"copies": copies, "note": "Added provenance only; original evidence and event payloads unchanged"}, facility_id)
    db.commit()


def metadata(db, digest):
    raw = path_for(digest).read_bytes()
    rows = db.scalars(select(models.Evidence).where(
        (models.Evidence.snapshot_hash == digest) | (models.Evidence.saved_copy_hash == digest)).order_by(models.Evidence.retrieved_at)).all()
    actual = hashlib.sha256(raw).hexdigest()
    return {"sha256": digest, "actual_sha256": actual, "match": actual == digest, "byte_count": len(raw),
            "copies": [{"source_url": e.url, "retrieved_at": e.retrieved_at, "source_time": e.source_time,
                        "source": e.source, "kind": e.kind, "copy_kind": e.saved_copy_kind or "raw",
                        "scenario_kind": e.scenario_kind or ("simulated" if e.source == "demo" or isinstance(e.value, dict) and e.value.get("simulated") else None)} for e in rows]}

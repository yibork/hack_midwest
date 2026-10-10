"""One local-signals pass for an evaluation: coordinator -> fetch -> pre-filter -> agents -> Evidence.

Stores one Evidence row per source fetch (kind "local_feed"; status "unknown" when it failed or
robots.txt disallowed it) and one per validated fact (kind "local_fact"). rules/local.py reads
those rows; nothing here decides a posture.
"""
import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from ... import record, schemas
from ...config import settings
from ...db import utcnow
from ...services import store_evidence_now
from ...sources import local_signals as ls
from ..runtime import new_state
from . import coordinator, specialists
from .prefilter import prefilter


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def fact_evidence(fact: dict, item: dict, src: ls.Source, agent: str, how: str, now: datetime,
                  facility: schemas.Facility) -> schemas.Evidence:
    dist = miles(facility.lat, facility.lon, item["lat"], item["lon"]) if "lat" in item and "lon" in item else None
    reported = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None
    routes = ", ".join(fact["routes_mentioned"])
    return schemas.Evidence(
        id=uuid.uuid4().hex, source="local", kind="local_fact",
        value={**fact, "agent": agent, "source_id": src.id, "source_name": src.name, "authority": src.authority,
               "title": item["title"], "link": item["link"], "reported_at": item.get("published_at"),
               "undated": bool(item.get("undated")), "extracted_by": how, "distance_miles": dist},
        source_time=reported, retrieved_at=now, valid_until=now + timedelta(minutes=src.refresh_minutes),
        url=item["link"], relevance_note=f"{agent}: {fact['fact_type'].replace('_', ' ')}"
                                         f"{' on ' + routes if routes else ''} ({src.name})",
        status="ok")


def run(facility: schemas.Facility, evaluation_id: str, alerts: list[dict], commute_routes: list[str],
        facility_county: str | None = None) -> list[schemas.Evidence]:
    now = utcnow()
    fac = {"name": facility.name, "access_routes": facility.access_routes, "county": facility_county}
    ids = {"evaluation_id": evaluation_id, "facility_id": facility.id}
    plan = coordinator.choose(alerts, fac, new_state(**ids), settings.agent_timeout)

    srcs = [s for s in ls.sources() if s.agent in plan["agents"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ls.fetch, srcs))
    evidence = [r.to_evidence() for r in results]

    by_agent: dict[str, list[dict]] = {}
    src_of: dict[str, ls.Source] = {}
    for r in results:
        for it in r.items:
            by_agent.setdefault(r.source.agent, []).append(
                {**it, "source_name": r.source.name, "authority": r.source.authority})
            src_of[it["item_id"]] = r.source
    work = {}
    for agent in plan["agents"]:
        items = by_agent.get(agent, [])
        relevant = prefilter(items, facility.access_routes, commute_routes)
        record.append_now("local.prefilter", {"agent": f"local_{agent}_agent", **ids, "items": len(items),
                                              "relevant": len(relevant)},
                          facility_id=facility.id, evaluation_id=evaluation_id)
        if relevant:
            work[agent] = relevant

    def one(agent: str):
        return agent, specialists.run_or_fallback(agent, work[agent], fac, commute_routes, new_state(**ids),
                                                  settings.local_agent_timeout,
                                                  use_model=agent in plan["model_agents"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(one, list(work)))
    for agent, (facts, how) in outputs:
        items = {it["item_id"]: it for it in work[agent]}
        for f in facts:
            evidence.append(fact_evidence(f, items[f["item_id"]], src_of[f["item_id"]], agent, how, now, facility))
    store_evidence_now(evidence, facility.id, evaluation_id)
    return evidence

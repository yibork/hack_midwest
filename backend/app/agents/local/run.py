"""One local-signals pass for an evaluation: coordinator -> fetch -> pre-filter -> agents -> Evidence.

Stores one Evidence row per source fetch (kind "local_feed"; status "unknown" when it failed or
robots.txt disallowed it) and one per validated fact (kind "local_fact"). rules/local.py reads
those rows; nothing here decides a posture.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from ... import record, schemas
from ...config import settings
from ...db import utcnow
from ...geo import Corridor, GeoContext, Segment
from ...routes import normalize_routes
from ...services import store_evidence_now
from ...sources import local_signals as ls
from ..runtime import new_state
from . import coordinator, specialists
from .prefilter import prefilter


def geo_context(facility: schemas.Facility, people: list[schemas.Employee]) -> GeoContext:
    fpt = (facility.lat, facility.lon)
    segments = tuple(Segment(route=seg.route, points=tuple(tuple(p) for p in seg.points), buffer_km=seg.buffer_km,
                             ramps=seg.ramps, name=seg.name) for seg in facility.access_routes if seg.points)
    corridors = tuple(Corridor(employee_id=p.id, routes=frozenset(normalize_routes(p.commute_routes)),
                               home=(p.home_lat, p.home_lon), facility=fpt)
                      for p in people if p.home_lat and p.home_lon and p.commute_routes)
    return GeoContext(facility=fpt, segments=segments, corridors=corridors)


def fact_evidence(fact: dict, item: dict, src: ls.Source, agent: str, how: str, now: datetime) -> schemas.Evidence:
    reported = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None
    routes = ", ".join(fact["routes_mentioned"])
    where = (f"{fact['distance_km']:g} km from facility" if fact.get("distance_km") is not None
             else "location uncertain (no coordinates)")
    return schemas.Evidence(
        id=uuid.uuid4().hex, source="local", kind="local_fact",
        value={**fact, "agent": agent, "source_id": src.id, "source_name": src.name, "authority": src.authority,
               "title": item["title"], "link": item["link"], "reported_at": item.get("published_at"),
               "undated": bool(item.get("undated")), "extracted_by": how, "points": item.get("points") or [],
               "category": item.get("category"), "active_since": item.get("active_since")},
        source_time=reported, retrieved_at=now, valid_until=now + timedelta(minutes=src.refresh_minutes),
        url=item["link"], relevance_note=f"{agent}: {fact['fact_type'].replace('_', ' ')}"
                                         f"{' on ' + routes if routes else ''}; {fact['relevance']}, {where} ({src.name})",
        status="ok")


def run(facility: schemas.Facility, evaluation_id: str, alerts: list[dict], people: list[schemas.Employee],
        facility_county: str | None = None, use_models: bool = True) -> list[schemas.Evidence]:
    """use_models=False (scheduled rechecks): rule-based coordination, roads by code extraction."""
    now = utcnow()
    commute_routes = sorted({r for p in people for r in p.commute_routes})
    geo = geo_context(facility, people)
    fac = {"name": facility.name, "access_routes": facility.route_names, "county": facility_county}
    ids = {"evaluation_id": evaluation_id, "facility_id": facility.id}
    plan = coordinator.choose(alerts, fac, new_state(**ids), settings.agent_timeout, use_model=use_models)

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
        relevant = prefilter(items, facility.route_names, commute_routes)
        record.append_now("local.prefilter", {"agent": f"local_{agent}_agent", **ids, "items": len(items),
                                              "relevant": len(relevant)},
                          facility_id=facility.id, evaluation_id=evaluation_id)
        if relevant:
            work[agent] = relevant

    def one(agent: str):
        return agent, specialists.run_or_fallback(agent, work[agent], fac, commute_routes, new_state(**ids),
                                                  settings.local_agent_timeout,
                                                  use_model=agent in plan["model_agents"], geo=geo)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(one, list(work)))
    for agent, (facts, how, rejected) in outputs:
        items = {it["item_id"]: it for it in work[agent]}
        for f in facts:
            evidence.append(fact_evidence(f, items[f["item_id"]], src_of[f["item_id"]], agent, how, now))
        # Per-agent result, so the UI can show what was rejected and why (no posture effect).
        evidence.append(schemas.Evidence(
            id=uuid.uuid4().hex, source="local", kind="local_agent_result", status="ok", retrieved_at=now,
            value={"agent": agent, "extracted_by": how, "items_in": len(items), "facts": len(facts),
                   "rejected": [{"reason": r["reason"], "title": r.get("title", "")[:120]} for r in rejected][:10]},
            relevance_note=f"{agent}: {len(facts)} fact(s), {len(rejected)} rejected"))
    store_evidence_now(evidence, facility.id, evaluation_id)
    return evidence

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
from ...sources import geocode
from . import coordinator, roads_cache, specialists
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
    n = len(fact.get("affected_employee_ids") or [])
    rel = f"commute (affects {n} commute{'s' if n != 1 else ''})" if fact["relevance"] == "commute" else fact["relevance"]
    return schemas.Evidence(
        id=uuid.uuid4().hex, source="local", kind="local_fact",
        value={**fact, "agent": agent, "source_id": src.id, "source_name": src.name, "authority": src.authority,
               "title": item["title"], "link": item["link"], "reported_at": item.get("published_at"),
               "undated": bool(item.get("undated")), "extracted_by": how, "points": item.get("points") or [],
               "category": item.get("category"), "active_since": item.get("active_since"),
               "snapshot_hash": item.get("snapshot_hash")},
        source_time=reported, retrieved_at=now, valid_until=now + timedelta(minutes=src.refresh_minutes),
        url=item["link"], relevance_note=f"{agent}: {fact['fact_type'].replace('_', ' ')}"
                                         f"{' on ' + routes if routes else ''}; {rel}, {where} ({src.name})",
        status="ok")


class LocalPass:
    """Phase A (fast): coordinator, fetch, pre-filter, roads cache lookup. Evidence for feeds and
    reused facts is stored at once. Phase B (finish): live agent runs, if any are pending."""

    def __init__(self, facility, evaluation_id, day, people, facility_county, use_models):
        self.facility, self.evaluation_id, self.day = facility, evaluation_id, day
        self.people, self.use_models = people, use_models
        self.commute_routes = sorted({r for p in people for r in p.commute_routes})
        self.geo = geo_context(facility, people)
        self.fac = {"name": facility.name, "access_routes": facility.route_names, "county": facility_county}
        self.ids = {"evaluation_id": evaluation_id, "facility_id": facility.id}
        self.plan: dict = {}
        self.work: dict[str, list[dict]] = {}
        self.src_of: dict[str, ls.Source] = {}
        self.pending: list[str] = []  # agents that need a live model call (phase B)
        self.roads_key: str | None = None
        self.roads_hashes: dict = {}

    # ---- phase A
    def start(self, alerts: list[dict]) -> "LocalPass":
        now = utcnow()
        self.plan = coordinator.choose(alerts, self.fac, new_state(**self.ids), settings.agent_timeout,
                                       use_model=self.use_models)
        srcs = [s for s in ls.sources() if s.agent in self.plan["agents"]]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(ls.fetch, srcs))
        evidence = [r.to_evidence() for r in results]
        by_agent: dict[str, list[dict]] = {}
        for r in results:
            for it in r.items:
                by_agent.setdefault(r.source.agent, []).append(
                    {**it, "source_name": r.source.name, "authority": r.source.authority,
                     "snapshot_hash": r.snapshot_hash})
                self.src_of[it["item_id"]] = r.source
        for agent in self.plan["agents"]:
            items = by_agent.get(agent, [])
            relevant = prefilter(items, self.facility.route_names, self.commute_routes)
            record.append_now("local.prefilter", {"agent": f"local_{agent}_agent", **self.ids, "items": len(items),
                                                  "relevant": len(relevant)},
                              facility_id=self.facility.id, evaluation_id=self.evaluation_id)
            if relevant:
                self.work[agent] = relevant

        if "roads" in self.plan["agents"]:
            self.roads_hashes = {r.source.id: r.content_hash for r in results if r.source.agent == "roads"}
            self.roads_key = roads_cache.cache_key(self.facility.id, self.day, roads_cache.geometry_signature(self.geo),
                                                   self.roads_hashes)
            hit = roads_cache.get(self.roads_key)
            if hit is not None:
                evidence += self._facts_from_cache(hit, now)
                self.work.pop("roads", None)
                record.append_now("local.roads_reused", {
                    "agent": "local_roads_agent", **self.ids, "note": roads_cache.summary(hit),
                    "cached_at": hit.created_at, "origin": hit.origin, "facts": len(hit.entries)},
                    facility_id=self.facility.id, evaluation_id=self.evaluation_id)
        self.pending = [a for a in self.work if a in self.plan["model_agents"]]
        # Code-only work (no model call) is fast: do it now.
        for agent in [a for a in self.work if a not in self.pending]:
            evidence += self._run_agent(agent, now)
        store_evidence_now(evidence, self.facility.id, self.evaluation_id)
        return self

    # ---- phase B
    def finish(self) -> list[schemas.Evidence]:
        if not self.pending:
            return []
        now = utcnow()
        with ThreadPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(lambda a: self._run_agent(a, now), self.pending))
        evidence = [e for part in parts for e in part]
        store_evidence_now(evidence, self.facility.id, self.evaluation_id)
        return evidence

    def _run_agent(self, agent: str, now) -> list[schemas.Evidence]:
        facts, how, rejected = specialists.run_or_fallback(
            agent, self.work[agent], self.fac, self.commute_routes, new_state(**self.ids),
            settings.local_agent_timeout, use_model=agent in self.plan["model_agents"], geo=self.geo)
        items = {it["item_id"]: it for it in self.work[agent]}
        for f in facts:
            if f.get("location_provenance", "none") == "none" and not f.get("geometry"):
                locate_by_text(f, items[f["item_id"]], self.geo)
        if agent == "roads" and how == "agent":
            roads_cache.put(self.roads_key, self.facility.id, self.day, self.roads_hashes,
                            [{"fact": f, "item": items[f["item_id"]], "source_id": self.src_of[f["item_id"]].id}
                             for f in facts], rejected, origin="evaluation", evaluation_id=self.evaluation_id)
        out = [fact_evidence(f, items[f["item_id"]], self.src_of[f["item_id"]], agent, how, now) for f in facts]
        out.append(agent_result(agent, how, len(items), facts, rejected, now))
        return out

    def _facts_from_cache(self, hit, now) -> list[schemas.Evidence]:
        srcs = {s.id: s for s in ls.sources()}
        out = [fact_evidence(e["fact"], e["item"], srcs[e["source_id"]], "roads", "cache", now)
               for e in hit.entries if e["source_id"] in srcs]
        out.append(agent_result("roads", "cache", None, [e["fact"] for e in hit.entries], hit.rejected, now,
                                note=roads_cache.summary(hit)))
        return out


def locate_by_text(fact: dict, item: dict, geo) -> None:
    """No source coordinates: geocode "route + cross street" text (Nominatim, cached, 1 req/s).
    A geocoded point may mark commute relevance; it never matches a facility access segment."""
    query = geocode.query_for(f"{item['title']} {item.get('excerpt', '')}")
    if not query:
        fact["location_note"] = f"{fact.get('location_note') or 'No coordinates'}; no route + cross street to geocode"
        return
    hit = geocode.geocode(query)
    if hit is None:
        fact["location_note"] = f"No coordinates; geocoding '{query}' found nothing"
        return
    rel = geo.assess([(hit["lat"], hit["lon"])], fact["routes_mentioned"])
    fact.update(
        geometry={"type": "Point", "coordinates": [hit["lon"], hit["lat"]]},
        location_provenance="geocoded_text",
        location_note=f"Geocoded by OpenStreetMap Nominatim from '{query}' (approximate: street, not the exact spot)",
        distance_km=rel.distance_km, matches_employee_commute_routes=rel.matches_employee_commute_routes,
        affected_employee_ids=rel.affected_employee_ids,
        # Approximate locations can't close a facility access route: commute relevance at most.
        matches_facility_access_route=False, segments=[],
        relevance="commute" if rel.affected_employee_ids else "not_relevant")


def agent_result(agent, how, items_in, facts, rejected, now, note=None) -> schemas.Evidence:
    """Per-agent result, so the UI can show what was rejected and why (no posture effect)."""
    return schemas.Evidence(
        id=uuid.uuid4().hex, source="local", kind="local_agent_result", status="ok", retrieved_at=now,
        value={"agent": agent, "extracted_by": how, "items_in": items_in, "facts": len(facts),
               "rejected": [{"reason": r["reason"], "title": r.get("title", "")[:120]} for r in rejected][:10]},
        relevance_note=note or f"{agent}: {len(facts)} fact(s), {len(rejected)} rejected")


def start(facility, evaluation_id, alerts, people, facility_county=None, use_models=True, day=None) -> LocalPass:
    return LocalPass(facility, evaluation_id, day or utcnow().date(), people, facility_county, use_models).start(alerts)


def run(facility: schemas.Facility, evaluation_id: str, alerts: list[dict], people: list[schemas.Employee],
        facility_county: str | None = None, use_models: bool = True, day=None) -> LocalPass:
    """Both phases, synchronously (scheduled rechecks, tests)."""
    lp = start(facility, evaluation_id, alerts, people, facility_county, use_models, day)
    lp.finish()
    return lp


def refresh_roads(facility: schemas.Facility, day, people: list[schemas.Employee]) -> dict:
    """Background: fetch road sources; if their content changed (no cache entry) and the 10-minute
    limit allows, run the roads agent now and cache the facts. No evaluation, no recommendation."""
    lp = LocalPass(facility, None, day, people, None, use_models=True)
    lp.plan = {"agents": ["roads"], "model_agents": ["roads"]}
    srcs = [s for s in ls.sources() if s.agent == "roads"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ls.fetch, srcs))
    items = []
    for r in results:
        for it in r.items:
            items.append({**it, "source_name": r.source.name, "authority": r.source.authority,
                          "snapshot_hash": r.snapshot_hash})
            lp.src_of[it["item_id"]] = r.source
    lp.roads_hashes = {r.source.id: r.content_hash for r in results}
    lp.roads_key = roads_cache.cache_key(facility.id, day, roads_cache.geometry_signature(lp.geo), lp.roads_hashes)
    result = {"facility_id": facility.id, "operating_date": day.isoformat(), "key": (lp.roads_key or "")[:12]}
    if lp.roads_key is None:
        return {**result, "action": "skipped", "reason": "a road source is unavailable"}
    if roads_cache.get(lp.roads_key) is not None:
        return {**result, "action": "unchanged"}
    previous = roads_cache.latest(facility.id, day)
    changed = sorted(k for k, v in lp.roads_hashes.items()
                     if not previous or previous.source_hashes.get(k) != v)
    record.append_now("roads.sources_changed", {"facility_id": facility.id, "operating_date": day.isoformat(),
                                                "changed_sources": changed}, facility_id=facility.id)
    if not roads_cache.background_allowed():
        return {**result, "action": "deferred", "reason": "one background roads run per 10 minutes", "changed": changed}
    lp.work["roads"] = prefilter(items, facility.route_names, lp.commute_routes)
    if not lp.work["roads"]:
        roads_cache.put(lp.roads_key, facility.id, day, lp.roads_hashes, [], [], origin="background")
        return {**result, "action": "cached", "facts": 0, "changed": changed}
    lp.ids = {"evaluation_id": None, "facility_id": facility.id, "llm_budget_key": f"roads-bg:{lp.roads_key[:12]}"}
    facts, how, rejected = specialists.run_or_fallback(
        "roads", lp.work["roads"], lp.fac, lp.commute_routes, new_state(**lp.ids), settings.local_agent_timeout,
        use_model=True, geo=lp.geo)
    if how == "agent":
        by_id = {it["item_id"]: it for it in lp.work["roads"]}
        roads_cache.put(lp.roads_key, facility.id, day, lp.roads_hashes,
                        [{"fact": f, "item": by_id[f["item_id"]], "source_id": lp.src_of[f["item_id"]].id} for f in facts],
                        rejected, origin="background")
    record.append_now("roads.background_run", {**result, "changed_sources": changed, "facts": len(facts),
                                               "extracted_by": how}, facility_id=facility.id)
    return {**result, "action": "ran", "facts": len(facts), "extracted_by": how, "changed": changed}

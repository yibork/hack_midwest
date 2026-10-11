"""The only thing local agents may return, and the code that checks it. Pure functions.

Code, not the model, decides: which items exist (item_id must be one we sent), which routes an
item mentions (must appear in the item text), whether its coordinates fall on a facility access
segment or an employee's commute corridor (app/geo.py), the source's authority, and whether a
"road_closure" is a full closure (lane, shoulder, ramp and exit closures are road_condition).
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ...geo import GeoContext
from ...routes import normalize_routes, primary_route, routes_in_text

FactType = Literal["road_closure", "road_condition", "school_closure", "school_early_dismissal", "power_outage",
                   "utility_disruption", "local_government_closure", "public_safety_incident", "other"]

AGENT_FACT_TYPES = {
    "roads": {"road_closure", "road_condition", "other"},
    "schools": {"school_closure", "school_early_dismissal", "other"},
    "public_safety": {"power_outage", "utility_disruption", "local_government_closure", "public_safety_incident",
                      "road_closure", "other"},
    "news": set(FactType.__args__),
}
MAX_QUOTE_WORDS = 25


class LocalFact(BaseModel):
    item_id: str
    fact_type: FactType
    location_text: str = Field(max_length=300)
    routes_mentioned: list[str] = []
    counties: list[str] = []
    effective_from: str | None = None  # when it starts/started (not when it was reported)
    effective_until: str | None = None
    matches_facility_access_route: bool = False
    matches_employee_commute_routes: list[str] = []
    supporting_quote: str = Field(max_length=400)
    confidence: Literal["high", "medium", "low"]


class LocalFacts(BaseModel):
    facts: list[LocalFact]


# ---------------------------------------------------------------- validation

_NORM = re.compile(r"[^a-z0-9]+")
PARTIAL_CLOSURE = re.compile(
    r"\b((left|right|center|middle|inside|outside|one|two|\d+)\s+lanes?\s+(is\s+|are\s+)?closed|lanes? closed|"
    r"shoulder\s+(is\s+)?closed|(on|off|entrance|exit)\s*-?\s*ramps?|ramp\b|exit closed|rest area|"
    r"height restriction|width restriction|weight restriction|lane restriction|lane closure|"
    r"\bWS-|weigh station|welcome center)",
    re.I)
FULL_CLOSURE = re.compile(r"\b(road|highway|interstate|bridge|all lanes)\s+(is\s+|are\s+)?closed\b|\bclosed in both directions\b"
                          r"|\bis closed (northbound|southbound|eastbound|westbound)\b|\ball lanes closed\b"
                          r"|\b(NB|SB|EB|WB)\s+CLOSED\b", re.I)  # KC Scout: "18TH ST EXWY NB CLOSED"
DIRECTIONS = re.compile(r"\b(north|south|east|west)bound\b|\b[NSEW]B\b|\bboth directions\b|\bin both directions\b", re.I)


def norm(text: str) -> str:
    return _NORM.sub(" ", (text or "").lower()).strip()


def is_full_closure(text: str) -> bool:
    """A mainline closure, not a lane/shoulder/ramp/exit closure or restriction."""
    if PARTIAL_CLOSURE.search(text) and not re.search(r"\ball lanes\b", text, re.I):
        return False
    return bool(FULL_CLOSURE.search(text))


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


RAMP = re.compile(r"\b(ramp|exit|on-ramp|off-ramp|RP)\b", re.I)


def validate(facts: list[LocalFact], items: dict[str, dict], agent: str, geo: GeoContext) -> tuple[list[dict], list[dict]]:
    """-> (accepted fact dicts, rejected [{item_id, reason}]). Never trusts the model for routes,
    location relevance, authority or quotes. Relevance comes from the item's coordinates:
    within an access segment's buffer -> facility; within an employee's corridor -> commute;
    no coordinates -> uncertain (shown, never triggers a rule or ROUTE_CLOSED)."""
    ok, bad = [], []
    for f in facts:
        item = items.get(f.item_id)
        if item is None:
            bad.append({"item_id": f.item_id, "reason": "item not found in the fetched sources"})
            continue
        if f.fact_type not in AGENT_FACT_TYPES[agent]:
            bad.append({"item_id": f.item_id, "title": item["title"], "reason": f"{agent} agent cannot report {f.fact_type.replace('_', ' ')}"})
            continue
        text = f"{item['title']} {item.get('excerpt', '')}"
        quote = " ".join(f.supporting_quote.split())
        if not quote or norm(quote) not in norm(text):
            bad.append({"item_id": f.item_id, "title": item["title"], "reason": "quote not found in source"})
            continue
        if len(quote.split()) > MAX_QUOTE_WORDS:
            bad.append({"item_id": f.item_id, "title": item["title"], "reason": f"quote longer than {MAX_QUOTE_WORDS} words"})
            continue
        in_text = routes_in_text(text)
        routes = sorted(normalize_routes(f.routes_mentioned) & in_text)
        fact_type = f.fact_type
        main = primary_route(item["title"])
        if fact_type == "road_closure":
            routes = [main] if main else []  # the closed road, not cross streets in the description
        points = [tuple(p) for p in item.get("points") or []]
        rel = geo.assess(points, routes)
        if fact_type == "road_closure" and not is_full_closure(text):
            # Lane/ramp/exit/weigh-station closures don't close the road, unless the ramp is part of
            # a defined access segment (segment.ramps).
            if not (RAMP.search(text) and rel.ramp_segment and not re.search(r"lane|shoulder|weigh|WS-", text, re.I)):
                fact_type = "road_condition"
        ok.append({
            "item_id": f.item_id, "fact_type": fact_type, "location_text": f.location_text.strip()[:300],
            "routes_mentioned": routes, "counties": [c.strip().title() for c in f.counties][:5],
            "effective_from": _iso(f.effective_from), "effective_until": _iso(f.effective_until),
            "relevance": rel.relevance, "distance_km": rel.distance_km, "segments": rel.segments,
            "geometry": item.get("geometry") if points else None,
            "location_provenance": "source_coordinates" if points else "none",
            "location_note": item.get("location_note") or ("source coordinates" if points else "no coordinates"),
            "matches_facility_access_route": rel.matches_facility_access_route,
            "matches_employee_commute_routes": rel.matches_employee_commute_routes,
            "affected_employee_ids": rel.affected_employee_ids,
            "supporting_quote": quote, "confidence": f.confidence,
            "downgraded": fact_type != f.fact_type,
        })
    return dedupe(ok), bad


def dedupe(facts: list[dict]) -> list[dict]:
    """Opposite directions of the same closure are one fact (directions are kept in the text)."""
    out: dict[tuple, dict] = {}
    for f in facts:
        loc = norm(DIRECTIONS.sub(" ", f["location_text"]))
        key = (f["fact_type"], tuple(f["routes_mentioned"]), loc)
        if key in out:
            kept = out[key]
            kept.setdefault("merged_item_ids", []).append(f["item_id"])
            kept["matches_employee_commute_routes"] = sorted(
                set(kept["matches_employee_commute_routes"]) | set(f["matches_employee_commute_routes"]))
            kept["affected_employee_ids"] = sorted(set(kept.get("affected_employee_ids", [])) | set(f.get("affected_employee_ids", [])))
            kept["matches_facility_access_route"] = kept["matches_facility_access_route"] or f["matches_facility_access_route"]
        else:
            out[key] = dict(f)
    return list(out.values())


# ---------------------------------------------------------------- code-only fallback (roads)

def roads_fallback(items: list[dict], geo: GeoContext, watched_routes: list[str]) -> list[dict]:
    """When no model call is possible: full closures on watched routes, from official DOT text."""
    watched = normalize_routes(watched_routes)
    facts = []
    for it in items:
        text = f"{it['title']} {it.get('excerpt', '')}"
        main = primary_route(it["title"])
        if main not in watched or not (is_full_closure(text) or RAMP.search(text)):
            continue
        facts.append(LocalFact(item_id=it["item_id"], fact_type="road_closure", location_text=it["title"][:300],
                               routes_mentioned=[main], supporting_quote=" ".join(it["title"].split()[:MAX_QUOTE_WORDS]),
                               confidence="medium", effective_from=it.get("effective_from")))
    accepted, _ = validate(facts, {it["item_id"]: it for it in items}, "roads", geo)
    return [f for f in accepted if f["fact_type"] == "road_closure"]

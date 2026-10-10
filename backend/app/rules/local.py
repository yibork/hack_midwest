"""Local signals rules: what validated local facts may change. Pure.

- access_route_closed: an official_state road closure on a facility access route during the
  commute window -> delayed_arrival if at least one access route stays open, telework if all are
  closed. Margin: "N of M access routes closed".
- Employee commute-route closures feed the people engine's ROUTE_CLOSED (notify/exposure.py);
  they never change the facility posture.
- School closures in the facility county or in 3+ employee home counties: a corroboration note;
  raises confidence one level only when a weather rule already fired.
- Official local public safety / utility facts in the facility area: "manager should check";
  corroborate when a weather rule fired.
- News: "manager should check" with the link; never corroborates, never changes the posture.
- Nothing here can lower the posture (the engine takes the most restrictive posture).
"""
from dataclasses import dataclass, field
from datetime import datetime

from ..routes import normalize_routes
from ..schemas import Evidence, Facility, Trigger

PUBLIC_SAFETY_TYPES = {"power_outage", "utility_disruption", "local_government_closure", "public_safety_incident"}
SCHOOL_TYPES = {"school_closure", "school_early_dismissal"}


@dataclass
class LocalOutcome:
    triggers: list[Trigger] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # added to confidence reasons
    lowers: list[str] = field(default_factory=list)  # lower confidence one level (missing official data)
    corroborations: list[str] = field(default_factory=list)  # raise confidence one level (max one)
    changes: list[str] = field(default_factory=list)  # added to "what would change"


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _in_window(v: dict, start: datetime, end: datetime) -> bool:
    a, b = _dt(v.get("effective_from")), _dt(v.get("effective_until"))
    return (a is None or a < end) and (b is None or b > start)


def _near(v: dict, radius: float) -> bool:
    """Unknown distance (sources without coordinates are metro-only) counts as near."""
    d = v.get("distance_miles")
    return d is None or d <= radius


def _link(v: dict) -> str:
    return f"\"{v.get('title', '')[:90]}\" ({v.get('source_name')}: {v.get('link')})"


def apply(facility: Facility, evidence: list[Evidence], commute: tuple[datetime, datetime], weather_fired: bool,
          policy: dict, facility_county: str | None = None, home_counties: dict[str, int] | None = None) -> LocalOutcome:
    out = LocalOutcome()
    citation = policy.get("citation", "facility policy v1 (TODO: verify)")
    facts = [e for e in evidence if e.kind == "local_fact" and e.status == "ok" and isinstance(e.value, dict)]

    # Missing official road data is not "no closures".
    for e in evidence:
        if e.kind == "local_feed" and e.status == "unknown" and isinstance(e.value, dict):
            v = e.value
            msg = f"{v.get('source_name')} unavailable ({v.get('fetch_status')}: {v.get('error')}); not treated as 'no closures'."
            (out.lowers if v.get("authority") == "official_state" else out.notes).append(msg)

    # 1. access_route_closed
    access = normalize_routes(facility.access_routes)
    closing = [e for e in facts if e.value.get("authority") == "official_state"
               and e.value.get("fact_type") == "road_closure" and e.value.get("matches_facility_access_route")
               and e.value.get("confidence") in policy.get("closure_confidence", ["high", "medium"])
               and _near(e.value, policy.get("access_route_radius_miles", 15)) and _in_window(e.value, *commute)]
    closed = sorted(set().union(*[set(e.value.get("routes_mentioned", [])) for e in closing]) & access) if closing else []
    if closed:
        n, m = len(closed), len(access)
        rule = policy.get("access_route_closed", {})
        posture = rule.get("all_routes_closed", "telework") if n >= m else rule.get("some_routes_closed", "delayed_arrival")
        sources = sorted({e.value.get("source_name") for e in closing})
        out.triggers.append(Trigger(
            rule_id="access_route_closed",
            description="Official state DOT road closure on a facility access route during the commute window",
            threshold="any facility access route closed during the commute window",
            observed=f"{', '.join(closed)} closed ({'; '.join(sources)})", margin=f"{n} of {m} access routes closed",
            citation=citation, posture=posture, evidence_id=closing[0].id))
        out.changes.append(f"Reopening of {', '.join(closed)} (per {', '.join(sources)}) would remove the "
                           f"access_route_closed trigger.")

    commute_closed = sorted({r for e in facts if e.value.get("authority") == "official_state"
                             and e.value.get("fact_type") == "road_closure"
                             and _near(e.value, policy.get("commute_radius_miles", 40))
                             for r in e.value.get("matches_employee_commute_routes", [])})
    if commute_closed:
        out.notes.append(f"Official road closures on employee commute routes ({', '.join(commute_closed)}) are "
                         f"applied per person (ROUTE_CLOSED); they don't change the facility posture.")

    # 2. Schools: corroboration only.
    schools = [e for e in facts if e.value.get("fact_type") in SCHOOL_TYPES]
    if schools:
        counties = {c.lower() for e in schools for c in e.value.get("counties", [])}
        homes = {c.lower() for c in (home_counties or {})}
        in_facility = bool(facility_county and facility_county.lower() in counties)
        in_homes = sorted(counties & homes)
        if in_facility or len(in_homes) >= policy.get("school_min_home_counties", 3):
            where = f"the facility county ({facility_county})" if in_facility else f"{len(in_homes)} employee home counties"
            if weather_fired:
                out.corroborations.append(f"Local signal corroborates: school closures/early dismissals in {where}.")
            else:
                out.notes.append(f"School closures/early dismissals reported in {where}; no weather rule fired, so "
                                 f"confidence is unchanged.")

    # 3. Official local public safety / utilities: check, corroborate with a fired rule.
    for e in facts:
        v = e.value
        if v.get("authority") != "official_local" or v.get("fact_type") not in PUBLIC_SAFETY_TYPES:
            continue
        counties = {c.lower() for c in v.get("counties", [])}
        if counties and facility_county and facility_county.lower() not in counties:
            continue
        out.notes.append(f"Manager should check (official local): {_link(v)}.")
        if weather_fired:
            out.corroborations.append(f"Local signal corroborates: {v.get('fact_type').replace('_', ' ')} "
                                      f"reported by {v.get('source_name')}.")

    # 4. News: check only.
    for e in facts:
        v = e.value
        if v.get("authority") == "news" and v.get("fact_type") not in SCHOOL_TYPES:
            out.notes.append(f"Manager should check (local news, unverified): {_link(v)}.")
    return out

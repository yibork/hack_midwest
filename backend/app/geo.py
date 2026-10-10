"""Geometry for road relevance. Pure functions, kilometres.

A road item matches the facility only if one of its points lies within an access segment's
buffer (and it is on that segment's route); it matches an employee only if it lies within their
commute corridor (straight line home -> facility, buffered) on one of their commute routes.
Items without coordinates are "uncertain": shown, but never trigger a rule or ROUTE_CLOSED.
"""
import math
from dataclasses import dataclass, field

from .routes import normalize_routes

EARTH_KM = 6371.0
CORRIDOR_BUFFER_KM = 3.0

Point = tuple[float, float]  # (lat, lon)


def haversine_km(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def _xy(p: Point, lat0: float) -> tuple[float, float]:
    """Local equirectangular projection (km); fine at metro scale."""
    return (math.radians(p[1]) * math.cos(math.radians(lat0)) * EARTH_KM, math.radians(p[0]) * EARTH_KM)


def point_to_polyline_km(p: Point, line: list[Point]) -> float:
    if not line:
        return math.inf
    if len(line) == 1:
        return haversine_km(p, line[0])
    px, py = _xy(p, p[0])
    best = math.inf
    for a, b in zip(line, line[1:]):
        ax, ay = _xy(a, p[0])
        bx, by = _xy(b, p[0])
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


@dataclass(frozen=True)
class Segment:
    route: str
    points: tuple[Point, ...]
    buffer_km: float = 2.0
    ramps: bool = False  # ramps inside this stretch are part of the access route
    name: str = ""


@dataclass(frozen=True)
class Corridor:
    employee_id: str
    routes: frozenset[str]
    home: Point
    facility: Point
    buffer_km: float = CORRIDOR_BUFFER_KM


@dataclass
class Relevance:
    distance_km: float | None
    relevance: str  # facility | commute | not_relevant | uncertain
    matches_facility_access_route: bool = False
    segments: list[str] = field(default_factory=list)
    ramp_segment: bool = False
    matches_employee_commute_routes: list[str] = field(default_factory=list)
    affected_employee_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GeoContext:
    facility: Point
    segments: tuple[Segment, ...] = ()
    corridors: tuple[Corridor, ...] = ()

    def assess(self, points: list[Point], routes: list[str] | set[str]) -> Relevance:
        if not points:
            return Relevance(distance_km=None, relevance="uncertain")
        routes = normalize_routes(routes)
        dist = min(haversine_km(p, self.facility) for p in points)
        hits = [s for s in self.segments if normalize_routes([s.route]) & routes
                and min(point_to_polyline_km(p, list(s.points)) for p in points) <= s.buffer_km]
        affected, matched = [], set()
        for c in self.corridors:
            on = routes & c.routes
            if on and min(point_to_polyline_km(p, [c.home, c.facility]) for p in points) <= c.buffer_km:
                affected.append(c.employee_id)
                matched |= on
        rel = "facility" if hits else "commute" if affected else "not_relevant"
        return Relevance(distance_km=round(dist, 1), relevance=rel, matches_facility_access_route=bool(hits),
                         segments=[s.name or s.route for s in hits], ramp_segment=any(s.ramps for s in hits),
                         matches_employee_commute_routes=sorted(matched), affected_employee_ids=sorted(affected))

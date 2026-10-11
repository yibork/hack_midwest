"""Map data for a recommendation: facility, access segments, aggregated commute corridors and
local facts with their geometry and location provenance.

Privacy: commute corridors are never returned per person. They are aggregated into ~2 km grid
cells; a cell is shown only if at least K corridors cross it, and cells near any home are dropped,
so no home location can be read off the map. No API response contains home coordinates.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas
from .geo import CORRIDOR_BUFFER_KM, haversine_km, point_to_polyline_km

CELL_LAT, CELL_LON = 0.02, 0.025  # ~2.2 km x 2.2 km at Kansas City
K_MIN = 3  # at least this many corridors per shown cell
HOME_CLEARANCE_KM = 3.0  # drop cells whose center is this close to any home (> cell half-diagonal + margin)


def corridor_cells(facility: tuple[float, float], homes: list[tuple[float, float]]) -> list[dict]:
    if not homes:
        return []
    lines = [[h, facility] for h in homes]
    pad = 0.04
    lats = [p[0] for p in homes] + [facility[0]]
    lons = [p[1] for p in homes] + [facility[1]]
    lat0, lat1 = min(lats) - pad, max(lats) + pad
    lon0, lon1 = min(lons) - pad, max(lons) + pad
    cells = []
    i = 0
    while lat0 + i * CELL_LAT < lat1:
        j = 0
        while lon0 + j * CELL_LON < lon1:
            s, w = round(lat0 + i * CELL_LAT, 4), round(lon0 + j * CELL_LON, 4)
            c = (s + CELL_LAT / 2, w + CELL_LON / 2)
            n = sum(1 for line in lines if point_to_polyline_km(c, line) <= CORRIDOR_BUFFER_KM)
            if n >= K_MIN and min(haversine_km(c, h) for h in homes) >= HOME_CLEARANCE_KM:
                cells.append({"bounds": [[s, w], [round(s + CELL_LAT, 4), round(w + CELL_LON, 4)]], "count": n})
            j += 1
        i += 1
    return cells


def map_for(db: Session, rec: models.Recommendation) -> dict:
    from .notify.plan import load_inputs

    facility = db.get(models.Facility, rec.facility_id)
    fac = schemas.Facility.model_validate(facility)
    people = load_inputs(db, facility, rec.operating_date)[0] if not fac.temporary else []
    homes = [] if fac.demo_origin_id else [(p.home_lat, p.home_lon) for p in people if p.home_lat and p.home_lon and p.commute_routes]
    facts, unmapped = [], []
    for e in db.scalars(select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids),
                                                      models.Evidence.kind == "local_fact")):
        v = e.value if isinstance(e.value, dict) else {}
        row = {
            "id": e.id, "agent": v.get("agent"), "fact_type": v.get("fact_type"), "relevance": v.get("relevance"),
            "location_provenance": v.get("location_provenance") or ("source_coordinates" if v.get("points") else "none"),
            "location_note": v.get("location_note"), "geometry": v.get("geometry"), "title": v.get("title"),
            "quote": v.get("supporting_quote"), "link": v.get("link"),
            "snapshot_url": f"/api/snapshots/{v['snapshot_hash']}" if v.get("snapshot_hash") else None,
            "active_since": v.get("active_since"), "distance_km": v.get("distance_km"),
            "source_id": v.get("source_id"), "source_name": v.get("source_name"), "authority": v.get("authority"),
            "category": v.get("category"), "affected_commutes": len(v.get("affected_employee_ids") or []),
            "segments": v.get("segments") or [],
        }
        if row["geometry"] is None and v.get("points"):  # facts stored before geometry existed
            pts = v["points"]
            row["geometry"] = ({"type": "Point", "coordinates": [pts[0][1], pts[0][0]]} if len(pts) == 1 else
                               {"type": "LineString", "coordinates": [[b, a] for a, b in pts]})
        (facts if row["geometry"] else unmapped).append(row)
    return {
        "facility": {"name": fac.name, "lat": fac.lat, "lon": fac.lon},
        "segments": [{"route": s.route, "name": s.name, "buffer_km": s.buffer_km, "ramps": s.ramps,
                      "coordinates": [[lon, lat] for lat, lon in s.points]} for s in fac.access_routes if s.points],
        "corridor_cells": corridor_cells((fac.lat, fac.lon), homes),
        "corridor_rule": "Home-zone exposure and commute geometry are not available for this location." if fac.demo_origin_id else f"Cells crossed by at least {K_MIN} commute corridors (home to facility, "
                         f"{CORRIDOR_BUFFER_KM:g} km wide); cells within {HOME_CLEARANCE_KM:g} km of any home are hidden.",
        "facts": facts,
        "unmapped": unmapped,
    }

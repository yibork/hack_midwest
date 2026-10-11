"""Location provenance per source, geocoded locations never closing an access route, the map
endpoint, and the rule that no API response exposes an employee's home coordinates."""
import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.agents.local import facts as F
from app.agents.local.run import fact_evidence, locate_by_text
from app.db import SessionLocal
from app.main import app
from app.mapdata import HOME_CLEARANCE_KM, K_MIN, corridor_cells
from app.geo import haversine_km
from app.rules.engine import evaluate
from app.seed import FACILITY_ID, HOMES, seed
from app.sources import geocode, local_signals as ls
from tests.test_local_signals import (DOWNTOWN_I35, FACILITY, GEO, NOW, alerts_ev, fact, item, serve, src,  # noqa: F401
                                      web, wind_ev)

MGR = {"X-Role": "facility_manager"}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture(autouse=True)
def fresh():
    ls.clear_cache()
    yield
    ls.clear_cache()


# ---------------------------------------------------------------- provenance per source

def test_modot_items_are_source_points(web):
    s = src("modot_traveler")
    serve(web, s.url, "modot.json")
    it = ls.fetch(s, now=NOW).items[0]
    assert it["location_provenance"] == "source_coordinates" and it["geometry"]["type"] == "Point"
    assert it["geometry"]["coordinates"] == [-94.58, 39.1]  # GeoJSON is [lon, lat]
    assert "GEOM" in it["location_note"] and "WGS84" in it["location_note"]


def test_kandrive_items_get_road_lines_from_map_features(web):
    s = src("kandrive_events")
    serve(web, s.url, "kandrive.json")
    serve(web, s.parse["geometry_url"], "kandrive_features.json")
    [it] = ls.fetch(s, now=NOW).items  # the Iola event is outside the KC box
    assert it["geometry"]["type"] == "MultiLineString" and len(it["points"]) == 3
    assert it["location_provenance"] == "source_coordinates" and "eventMapFeatures" in it["location_note"]


def test_kandrive_falls_back_to_event_points_without_features(web):
    s = src("kandrive_events")
    serve(web, s.url, "kandrive.json")  # geometry_url not served -> 404
    [it] = ls.fetch(s, now=NOW).items
    assert it["geometry"]["type"] == "Point" and it["location_provenance"] == "source_coordinates"


def test_kcscout_items_are_source_points(web):
    s = src("kcscout_incidents")
    serve(web, s.url, "kcscout.json")
    it = ls.fetch(s, now=NOW).items[0]
    assert it["geometry"] == {"type": "Point", "coordinates": [-94.3478, 39.0369]}
    assert "Latitude/Longitude" in it["location_note"]


def test_rss_items_have_no_location(web):
    s = src("kshb_local_news")
    serve(web, s.url, "rss.xml")
    it = ls.fetch(s, now=datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)).items[0]
    assert it["location_provenance"] == "none" and "geometry" not in it


def test_every_registry_source_documents_its_coordinates():
    for s in ls.sources():
        assert s.coordinates.get("geometry") and s.coordinates.get("note"), s.id


# ---------------------------------------------------------------- geocoding

def test_geocoded_location_never_triggers_access_route_closed(monkeypatch):
    """News text 'I-35 closed at W 12th St' geocodes onto the downtown I-35 segment: still no rule."""
    monkeypatch.setattr(geocode, "geocode", lambda q: {"lat": DOWNTOWN_I35[0], "lon": DOWNTOWN_I35[1], "display_name": q})
    it = item("I-35 northbound closed at W 12th St", "Road closed. I-35 northbound closed at W 12th St downtown.", iid="n")
    [f], _ = F.validate([fact("n", supporting_quote="I-35 northbound closed at W 12th St")], {"n": it}, "news", GEO)
    assert f["location_provenance"] == "none" and f["relevance"] == "uncertain"
    locate_by_text(f, it, GEO)
    assert f["location_provenance"] == "geocoded_text" and "W 12th St, Kansas City" in f["location_note"]
    assert f["geometry"]["type"] == "Point" and f["distance_km"] < 3
    assert f["matches_facility_access_route"] is False  # approximate: can't close an access route
    ev = fact_evidence(f, it, src("modot_traveler"), "roads", "agent", NOW)  # even if it came from a DOT source
    ev.value["fact_type"] = "road_closure"
    assert evaluate(FACILITY, [alerts_ev(), wind_ev(), ev], NOW).posture == "normal"
    # the same fact with source coordinates does close it
    ev.value.update(location_provenance="source_coordinates", relevance="facility", matches_facility_access_route=True)
    assert evaluate(FACILITY, [alerts_ev(), wind_ev(), ev], NOW).posture == "delayed_arrival"


def test_geocoded_location_can_mark_commute_relevance(monkeypatch):
    from tests.test_local_signals import OP  # noqa: F401 (corridor Overland Park -> downtown on I-435)
    monkeypatch.setattr(geocode, "geocode", lambda q: {"lat": 39.0300, "lon": -94.6350, "display_name": q})
    it = item("I-435 crash near Antioch Rd", "I-435 northbound crash near Antioch Rd, lanes blocked", iid="c")
    [f], _ = F.validate([fact("c", fact_type="road_condition", routes_mentioned=["I-435"], supporting_quote="I-435 northbound crash")],
                        {"c": it}, "roads", GEO)
    locate_by_text(f, it, GEO)
    assert f["location_provenance"] == "geocoded_text" and f["relevance"] == "commute" and f["affected_employee_ids"] == ["op"]


def test_no_route_and_cross_street_means_no_location():
    it = item("Water main break downtown", "Crews are working on a water main break.", iid="w")
    [f], _ = F.validate([fact("w", fact_type="other", routes_mentioned=[],
                              supporting_quote="water main break")], {"w": it}, "news", GEO)
    locate_by_text(f, it, GEO)
    assert f["location_provenance"] == "none" and f["relevance"] == "uncertain" and "no route + cross street" in f["location_note"]


def test_geocoder_caches_and_respects_disabled_setting(monkeypatch):
    with SessionLocal() as s:
        s.merge(models.GeocodeCache(query="Paseo Blvd, Kansas City", lat=39.1, lon=-94.56, display_name="Paseo",
                                    provider="nominatim"))
        s.commit()
    monkeypatch.setattr(geocode.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    assert geocode.geocode("Paseo Blvd, Kansas City")["lat"] == 39.1  # from the cache
    assert geocode.geocode("Uncached St, Kansas City") is None  # GEOCODE=0 in tests: never calls out


# ---------------------------------------------------------------- privacy: no home coordinates anywhere

def _numbers(obj) -> set[float]:
    return {round(float(x), 4) for x in re.findall(r"-?\d+\.\d+", json.dumps(obj))}


def test_no_api_response_exposes_home_coordinates():
    client = TestClient(app)
    with SessionLocal() as db:
        rec = models.Recommendation(
            id=uuid.uuid4().hex, facility_id=FACILITY_ID, evaluated_at=datetime.now(timezone.utc),
            operating_date=datetime.now(timezone.utc).date(), posture="normal", triggers=[], confidence="high",
            confidence_reasons=[], what_would_change=[], rationale="t", rationale_source="template", evidence_ids=[],
            policy_version="t")
        db.add(rec)
        db.commit()
        homes = {(round(e.home_lat, 4), round(e.home_lon, 4)) for e in db.query(models.Employee).all()}
    responses = {
        "viewers": client.get("/api/viewers").json(),
        "recommendation": client.get(f"/api/recommendations/{rec.id}", headers=MGR).json(),
        "map": client.get(f"/api/recommendations/{rec.id}/map", headers=MGR).json(),
        "record": client.get("/api/record", headers=MGR).json(),
        "sources": client.get("/api/sources", headers=MGR).json(),
        "access": client.get(f"/api/facilities/{FACILITY_ID}/access-list", headers={"X-Role": "security_desk"}).json(),
        "team": client.get("/api/team", headers={"X-Role": "supervisor", "X-Person": "emp-002"}).json(),
        "me": client.get("/api/me", headers={"X-Role": "employee", "X-Person": "emp-005"}).json(),
    }
    for name, body in responses.items():
        nums = _numbers(body)
        leaked = [h for h in homes if h[0] in nums and h[1] in nums]
        assert not leaked, f"{name} exposes home coordinates {leaked}"
        assert "home_lat" not in json.dumps(body) and "home_lon" not in json.dumps(body), name
    cells = responses["map"]["corridor_cells"]
    assert cells and all(c["count"] >= K_MIN for c in cells)


def test_corridor_cells_never_include_a_home():
    homes = [(lat, lon) for lat, lon, _, _ in HOMES.values()]
    for c in corridor_cells((39.0997, -94.5786), homes):
        (s, w), (n, e) = c["bounds"]
        assert c["count"] >= K_MIN
        for h in homes:
            assert not (s <= h[0] <= n and w <= h[1] <= e), "a cell contains a home"
            assert haversine_km(((s + n) / 2, (w + e) / 2), h) >= HOME_CLEARANCE_KM

"""AirNow fixtures only. No provider or inference calls; real secrets are never loaded."""
import hashlib
import json
import logging
from dataclasses import replace
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from app import schemas, record
from app.sources import airnow
from app.rules.engine import evaluate
from .test_rules import FACILITY, NOW, alerts_ev, wind_ev
from .test_demo_scenarios import demo_db
from .test_investigations import scenario, refresh_state

DAY = NOW.astimezone(__import__('zoneinfo').ZoneInfo(FACILITY.timezone)).date()
KEY = "fake-airnow-secret-NEVER-STORE"


def observed(aqi=64, *, hour=4, pollutant="PM2.5"):
    return {"dateObserved": str(DAY), "hourObserved": hour, "localTimeZone": "CDT",
            "reportingAreaName": "Kansas City", "parameterName": pollutant,
            "nowcastAQI": aqi, "aqiCategoryName": "provider category"}


def forecast(aqi=64, day=DAY, pollutant="O3"):
    return {"dateIssue": str(DAY), "dateValid": str(day), "reportingArea": "Kansas City",
            "parameterName": pollutant, "aqi": aqi, "categoryName": "provider category"}


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.setattr(airnow, "settings", replace(airnow.settings, airnow_api_key=KEY))
    monkeypatch.setattr(record, "settings", replace(record.settings, snapshot_dir=tmp_path))
    calls = []
    def install(current=None, predicted=None):
        def fetch(url):
            calls.append(url)
            return json.dumps(current if "/observation/" in url else predicted).encode()
        monkeypatch.setattr(airnow, "_fetch", fetch)
    return install, calls, tmp_path


def collected(source, current=None, predicted=None, day=DAY):
    source[0]([observed()] if current is None else current, [forecast()] if predicted is None else predicted)
    return airnow.collect(FACILITY, day, now=NOW)


def test_replacement_endpoints_schema_radius_saved_hash_and_key_security(source, caplog):
    caplog.set_level(logging.DEBUG)
    row = observed()
    row.update({"API_KEY": KEY, "discussion": f"untrusted URL https://example.test/?API_KEY={KEY}"})
    evidence = collected(source, [row, observed(32, pollutant="O3")])
    assert [urlsplit(u).path for u in source[1]] == ["/aq/observation/current/ziplatlong/", "/aq/forecast/current/"]
    assert all(parse_qs(urlsplit(u).query)["distance"] == ["25"] for u in source[1])
    for ev in evidence:
        assert ev.status == "ok"
        assert "API_KEY" not in ev.url and KEY not in ev.model_dump_json()
        content = (source[2] / (ev.snapshot_hash + ".json")).read_bytes()
        assert KEY.encode() not in content
        assert hashlib.sha256(content).hexdigest() == ev.snapshot_hash
        assert ev.saved_copy_kind == "normalized"
    assert evidence[0].value["pollutants"][0]["category"] == "Moderate"
    assert {r["pollutant"] for r in evidence[0].value["pollutants"]} == {"PM2.5", "ozone"}
    assert KEY not in caplog.text and KEY not in repr(airnow.settings)


@pytest.mark.parametrize("body", [[], {"error": "no data"}, [observed(-1)], [observed(True)]])
def test_missing_numeric_data_is_unknown_never_good(source, body):
    evidence = collected(source, body, body)
    assert all(e.status == "unknown" for e in evidence)
    assert not airnow.candidates(evidence, DAY, NOW, FACILITY.timezone)


def test_missing_key_never_fetches(source, monkeypatch):
    source[0]([observed()], [forecast()])
    monkeypatch.setattr(airnow, "settings", replace(airnow.settings, airnow_api_key=""))
    evidence = airnow.collect(FACILITY, DAY, now=NOW)
    assert source[1] == [] and all(e.status == "unknown" for e in evidence)


def test_provider_error_never_stores_exception_url_key_or_body(source, monkeypatch, caplog):
    def fail(url):
        raise HTTPError(url, 403, f"bad key {KEY}", {}, None)
    monkeypatch.setattr(airnow, "_fetch", fail)
    evidence = airnow.collect(FACILITY, DAY, now=NOW)
    assert all(e.status == "unknown" and "HTTP 403" in e.value["error"] for e in evidence)
    assert KEY not in caplog.text + json.dumps([e.model_dump(mode="json") for e in evidence])


@pytest.mark.parametrize("aqi,posture,rule", [(100,"normal",None),(101,"normal","air_quality_sensitive"),
    (150,"normal","air_quality_sensitive"),(151,"telework","air_quality_telework"),
    (200,"telework","air_quality_telework"),(201,"closure","air_quality_closure"),
    (301,"closure","air_quality_closure")])
def test_exact_policy_boundaries(source, aqi, posture, rule):
    evidence = collected(source, [observed(aqi)], [forecast(aqi)])
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(), *evidence], NOW, operating_date=DAY)
    assert rec.posture == posture
    assert [t.rule_id for t in rec.triggers] == ([rule] if rule else [])
    if rule:
        assert rec.triggers[0].units == "AQI" and rec.triggers[0].evidence_id in {e.id for e in evidence}
        assert "peak gust of" not in rec.rationale
        assert rec.triggers[0].citation["publisher"] == "U.S. EPA / AirNow"


def test_higher_value_wins_and_air_quality_never_lowers_other_posture(source):
    ev = collected(source, [observed(225)], [forecast(110)])
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(), *ev], NOW, operating_date=DAY)
    assert rec.posture == "closure" and len(rec.triggers) == 1
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(default=65), *collected(source)], NOW, operating_date=DAY)
    assert rec.posture == "closure"


def test_future_target_uses_only_its_forecast(source):
    tomorrow = DAY + timedelta(days=1)
    ev = collected(source, [observed(250)], [forecast(64, tomorrow), forecast(250)], tomorrow)
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(), *ev], NOW, operating_date=tomorrow)
    assert rec.posture == "normal"
    assert airnow.candidates(ev, tomorrow, NOW, FACILITY.timezone)[0][0] == 64
    assert any("AQI 64 / 151" in s for s in rec.what_would_change)


def test_observation_age_not_fetch_age_and_each_pollutant_is_checked(source):
    ev = collected(source, [observed(250, hour=1), observed(32, pollutant="O3")], [])
    assert ev[0].status == "ok"  # ozone fresh; PM2.5 individually stale
    assert [v[0] for v in airnow.candidates(ev, DAY, NOW, FACILITY.timezone)] == [32]
    assert not airnow.candidates(ev, DAY, NOW + timedelta(hours=3), FACILITY.timezone)
    stale = collected(source, [observed(250, hour=1)], [])
    assert stale[0].status == "stale"


def test_redirects_are_blocked_before_credentials_can_leave_allowlist():
    with pytest.raises(ValueError, match="redirect blocked"):
        airnow.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test")


def test_sensitive_flag_only_during_operating_day(source):
    ev = collected(source, [observed(125)], [forecast(125)])
    after_close = NOW + timedelta(hours=12)
    ev = [e.model_copy(update={"retrieved_at": after_close, "valid_until": after_close + timedelta(hours=2)}) for e in ev]
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(), *ev], after_close, operating_date=DAY)
    assert not rec.triggers


def test_sensitive_accommodation_has_generic_supervisor_suggestion_not_medical_detail():
    from app.notify.impact import Exposure
    from app.notify.plan import build_plan
    from .test_impact import ctx, person, shift, here
    sensitive = person(id="p1", supervisor_id="sup", accommodation_needs=["air_quality_sensitive"], accommodation_on_file=True)
    supervisor = person(id="sup", role="supervisor")
    regular = person(id="p2", supervisor_id="sup", accommodation_needs=["early_notice"])
    plan = build_plan(ctx("normal"), [sensitive, supervisor, regular], here("p1")+here("sup")+here("p2"),
        [shift(pid="p1"), shift(pid="sup"), shift(pid="p2")], [], Exposure(air_quality_sensitive=True), base_url="http://test")
    suggestions = [i for i in plan.impacts if i.supervisor_suggestion]
    assert [i.employee.id for i in suggestions] == ["p1"]
    assert "accommodation on file" in suggestions[0].audience_text
    assert suggestions[0].message_type == "none"
    summaries = [p for p in plan.planned if p.message_type == "supervisor_summary" and p.included]
    assert len(summaries) == 1 and summaries[0].employee.id == "sup"
    assert "unscheduled telework" in summaries[0].message
    assert "air_quality_sensitive" not in summaries[0].message
    assert plan.summary().supervisor_suggestion_count == 1


@pytest.mark.parametrize("status", ["telework", "leave", "day_off"])
def test_no_air_quality_suggestion_when_not_scheduled_onsite(status):
    from app.notify.impact import Exposure
    from .test_impact import impact, person, shift
    result = impact("normal", p=person(accommodation_needs=["air_quality_sensitive"]),
        shifts=[shift(status)], exposure=Exposure(air_quality_sensitive=True))
    assert result.supervisor_suggestion is None


def test_seed_two_private_needs_idempotent_public_employee_has_no_details(demo_db):
    from sqlalchemy import select
    from app import models, seed
    seed.seed()
    sensitive = [p for p in demo_db.scalars(select(models.Employee)) if "air_quality_sensitive" in p.accommodation_needs]
    assert len(sensitive) == 2
    for p in sensitive:
        assert p.accommodation_on_file
        assert p.accommodation_needs.count("air_quality_sensitive") == 1
        public = schemas.EmployeePublic.model_validate(p).model_dump_json()
        assert "air_quality_sensitive" not in public and "accommodation_needs" not in public


def test_persisted_evidence_and_record_chain_have_no_secret(source, demo_db):
    from sqlalchemy import select
    from app import models, services
    evs = collected(source, [{**observed(), "API_KEY": KEY, "note": KEY}])
    services.store_evidence(demo_db, evs, "usda-kc-demo", "aq-check")
    demo_db.commit()
    events = demo_db.scalars(select(models.RecordEvent).where(models.RecordEvent.evaluation_id == "aq-check")).all()
    assert events and KEY not in json.dumps([e.payload for e in events])
    assert record.verify(demo_db, "usda-kc-demo")["ok"]


def test_people_plan_reads_target_day_air_quality_and_supervisor_board_is_private(source, demo_db, monkeypatch):
    from app import models, services, seed, people_api
    from app.notify import notices
    from app.roles import Viewer
    evs = collected(source, [observed(125)], [forecast(125)])
    rec = evaluate(schemas.Facility.model_validate(demo_db.get(models.Facility, seed.FACILITY_ID)),
        [alerts_ev(), wind_ev(), *evs], NOW, operating_date=DAY, target_mode="demo_workday")
    services.store_evidence(demo_db, evs, rec.facility_id, "aq-plan")
    row = models.Recommendation(**rec.model_dump())
    row.evidence_ids = [e.id for e in evs]
    demo_db.add(row)
    demo_db.commit()
    plan = notices.plan_for(demo_db, demo_db.get(models.Facility, row.facility_id), row, row.posture, now=NOW)
    assert plan.ctx.posture == "normal"
    eligible = [i for i in plan.impacts if i.scheduled == "onsite" and i.shift
                and i.shift.overlaps(plan.ctx.open_dt, plan.ctx.close_dt)
                and "air_quality_sensitive" in i.employee.accommodation_needs]
    assert len(eligible) == plan.summary().supervisor_suggestion_count > 0
    monkeypatch.setattr(people_api, "live_plan", lambda db, f: plan)
    sup = demo_db.get(models.Employee, seed.emp_id("sam")).supervisor_id
    board = people_api.team(supervisor_id=sup, db=demo_db, v=Viewer(role="supervisor", person_id=sup))
    assert board.suggestions and all("accommodation on file" in s.audience_text.lower() for s in board.suggestions)
    assert "air_quality_sensitive" not in board.model_dump_json()


def test_rel_location_is_used_for_nationwide_airnow_collection(source):
    facility = FACILITY.model_copy(update={"id": "demo-relocated-123", "lat": 34.1, "lon": -118.2,
        "timezone": "America/Los_Angeles", "demo_origin_id": "usda-kc-demo"})
    source[0]([observed()], [forecast()])
    airnow.collect(facility, DAY, now=NOW)
    assert all(parse_qs(urlsplit(u).query)["latitude"] == ["34.1"] for u in source[1])
    assert all(parse_qs(urlsplit(u).query)["longitude"] == ["-118.2"] for u in source[1])


def test_air_quality_rule_links_and_writer_metric_facts(source):
    from app.services import change_rule_id
    from app.agents.writer_agent import build_facts, validate_rationale
    evs = collected(source, [observed(64)], [forecast(64)])
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev(), *evs], NOW, operating_date=DAY)
    assert change_rule_id(next(s for s in rec.what_would_change if "AQI 64 / 151" in s)) == "air_quality_telework"
    facts = build_facts(rec, evs, FACILITY.name, FACILITY.timezone, FACILITY)
    assert facts["metric_threshold_pairs"]["aqi"]["observed"] == [64, 64]
    assert validate_rationale("Normal operations: AQI 64 is below the 151 telework threshold.", "normal", facts) is None
    facts["other_metric"] = 46
    assert "metric mismatch" in validate_rationale("Normal operations: AQI 64 is below the 46 mph threshold.", "normal", facts)


@pytest.mark.parametrize("failure", [False, True])
def test_stale_airnow_refetch_is_deterministic_and_history_is_immutable(scenario, monkeypatch, failure):
    import uuid
    from app import investigations as inv, services
    from app.db import utcnow
    db, rec, fac, _ = scenario
    prior = schemas.Evidence(id=uuid.uuid4().hex, source="airnow", kind="air_quality_forecast", status="ok",
        value={"target_date": str(rec.operating_date), "pollutants": []},
        retrieved_at=utcnow()-timedelta(hours=3), valid_until=utcnow()-timedelta(hours=1))
    services.store_evidence(db, [prior], fac.id, rec.evaluation_id)
    db.commit()
    calls = []
    def collect(facility, day):
        calls.append((facility.id, day))
        return [prior.model_copy(update={"id": uuid.uuid4().hex, "retrieved_at": utcnow(),
            "valid_until": utcnow()+timedelta(hours=2), "status": "unknown" if failure else "ok",
            "value": {"error": "Provider failed"} if failure else prior.value})]
    monkeypatch.setattr(airnow, "collect", collect)
    monkeypatch.setattr(inv, "run_check", lambda *a: pytest.fail("AirNow refresh must not run other tools/models"))
    result = inv.refresh_expired([prior], refresh_state(rec, fac))
    assert calls == [(fac.id, rec.operating_date)]
    refreshed = max((e for e in result if e.kind == prior.kind), key=lambda e: e.retrieved_at)
    assert refreshed.id != prior.id and refreshed.status == ("stale" if failure else "ok")
    if failure:
        assert "last_successful_retrieved_at" in refreshed.value
    assert prior.status == "ok" and prior.valid_until < utcnow()


def test_simulated_and_replay_sources_never_fetch_live_airnow(scenario, monkeypatch):
    from app import investigations as inv
    db, rec, fac, _ = scenario
    monkeypatch.setattr(airnow, "collect", lambda *a, **k: pytest.fail("Scenario must not fetch live AirNow"))
    for mode in ("simulated", "replay"):
        old = schemas.Evidence(id=mode, source="airnow", kind="air_quality_current", scenario_kind=mode,
            status="stale", retrieved_at=NOW-timedelta(hours=3), valid_until=NOW-timedelta(hours=2), value={})
        assert inv.refresh_expired([old], refresh_state(rec, fac)) == [old]

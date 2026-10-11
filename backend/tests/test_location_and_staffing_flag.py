"""Location applicability and default-off staffing; all evidence/providers are fixtures."""
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import demo_location, models, scheduler, schemas, seed, services, people_api, memo
from app.main import app
from app.notify import notices, plan as plan_module, escalation
from app.rules.engine import evaluate, compute_windows, load_policy
from app.sources import airnow, local_signals, nwps
from tests.test_demo_scenarios import demo_db, storm
from tests.test_rules import FACILITY, NOW, alerts_ev, wind_ev, alert, local


def test_kc_coverage_excluded_but_applicable_failure_lowers_confidence():
    excluded = [f.to_evidence() for f in demo_location.local_feeds(FACILITY)]
    homes = demo_location.unknown("home_zone_alerts")
    result = evaluate(FACILITY, [alerts_ev(), wind_ev(), *excluded, homes], NOW)
    assert result.confidence == "high"
    assert result.confidence_reasons.count(f"{len(excluded)} Kansas City-only sources not applicable here") == 1
    assert not any("unavailable" in r for r in result.confidence_reasons)
    failure = schemas.Evidence(id="failed", source="nws", kind="hourly_forecast", status="unknown",
        retrieved_at=NOW, value={"error": "Provider failed"})
    assert evaluate(FACILITY, [alerts_ev(), wind_ev(), *excluded, homes, failure], NOW).confidence == "low"


def test_flood_and_wind_citations_use_confirmed_local_office():
    facility = FACILITY.model_copy(update={"nws_office": "TAE", "demo_origin_id": "kc"})
    result = evaluate(facility, [alerts_ev(alert("Flood Warning", local(4), local(20))), wind_ev(default=60)], NOW)
    citations = {t.rule_id: t.citation for t in result.triggers}
    assert citations["flood"]["url"] == "https://www.weather.gov/safety/flood-watch-warning"
    assert "pd01005015curr.pdf" in citations["high_wind"]["url"]
    for citation in citations.values():
        assert citation["local_office"] == "TAE"
        assert "Kansas City" not in json.dumps(citation)


def test_alert_span_has_both_dates_and_facility_timezone():
    start = datetime(2026, 10, 11, 21, 13, tzinfo=local(4).tzinfo)
    end = datetime(2026, 10, 13, 19, tzinfo=start.tzinfo)
    result = evaluate(FACILITY, [alerts_ev(alert("Flood Warning", start, end)), wind_ev()], NOW)
    flood = next(t for t in result.triggers if t.rule_id == "flood")
    assert "Sun Oct 11 9:13 PM CDT - Tue Oct 13 7:00 PM CDT" in flood.observed


def test_live_alert_jobs_hourly_until_deadline_and_at_expiry(demo_db, monkeypatch):
    now = datetime(2026, 10, 11, 0, 5, tzinfo=timezone.utc)
    deadline, end = now+timedelta(hours=3), now+timedelta(days=2)
    facility = demo_db.get(models.Facility, seed.FACILITY_ID)
    rec = SimpleNamespace(id="saved", operating_date=now.date(), posture="telework")
    monkeypatch.setattr(notices, "plan_for", lambda *a: SimpleNamespace(clock=SimpleNamespace(at=deadline)))
    ev = alerts_ev({"ends": end.isoformat()})
    scheduler.schedule_live_alert(demo_db, facility, rec, [ev], now=now)
    scheduler.schedule_live_alert(demo_db, facility, rec, [ev], now=now+timedelta(minutes=2))
    jobs = list(demo_db.scalars(select(models.ScheduledJob)))
    hours = [j for j in jobs if j.kind == "live_alert_hourly"]
    assert len(hours) == 3 and all(now < j.run_at <= deadline for j in hours)
    expires = [j for j in jobs if j.kind == "live_alert_expiry"]
    assert len(expires) == 1 and expires[0].run_at == end
    assert scheduler.next_run(demo_db, facility.id, now.date()) == min(j.run_at for j in hours)


def disable_staffing(monkeypatch):
    for module in (plan_module, notices, people_api, memo, escalation):
        monkeypatch.setattr(module, "settings", replace(module.settings, staffing_feature=False))


def test_flag_off_hides_coverage_keeps_emergency_duties_and_clock(demo_db, monkeypatch):
    rec = storm(demo_db)
    facility = demo_db.get(models.Facility, rec.facility_id)
    enabled = notices.plan_for(demo_db, facility, rec, rec.posture)
    assert enabled.coverage
    disable_staffing(monkeypatch)
    disabled = notices.plan_for(demo_db, facility, rec, rec.posture)
    assert disabled.coverage == [] and disabled.summary().staffing_feature is False
    assert disabled.clock == enabled.clock
    assert [(i.employee.id, i.duty_instruction, i.requires_ack, i.access) for i in enabled.impacts] == [
        (i.employee.id, i.duty_instruction, i.requires_ack, i.access) for i in disabled.impacts]
    dana = next(p for p in disabled.planned if p.employee.id == seed.emp_id("ava"))
    assert dana.requires_ack and "report" in dana.duty_instruction.lower()
    assert disabled.summary().emergency_staff_reporting > 0
    assert not any(p.message_type in {"staffing_alert", "call_in"} for p in disabled.planned)
    digest = notices.manager_digest(disabled, "NWS Winter Storm Warning")
    assert not any(t in digest.lower() for t in ("shortfall", "critical function", "backup", "call them"))
    assert rec.confidence == "high" and rec.posture == "closure"
    decision = services.record_decision(demo_db, rec, schemas.DecisionIn(action="approve", manager_name="Maria"))
    page = memo.render(demo_db, decision)
    assert "Call-ins" not in page and "STAFFING SHORTFALL" not in page
    notice = demo_db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    assert people_api.notice_detail(demo_db, notice).staffing_feature is False
    response = TestClient(app).post(f"/api/notices/{notice.id}/call-ins", headers={"X-Role": "facility_manager"},
        json={"employee_id": seed.emp_id("jordan"), "function_code": "animal_care", "manager_name": "Maria"})
    assert response.status_code == 403


def test_airnow_provider_no_coverage_and_empty_list_are_not_parsing_failures(monkeypatch):
    monkeypatch.setattr(airnow, "settings", replace(airnow.settings, airnow_api_key="fixture-key"))
    monkeypatch.setattr(airnow, "_fetch", lambda u: json.dumps({"WebServiceError": [{"Message": "There are no observations available for the requested latitude/longitude"}]}).encode())
    evidence = airnow.collect(FACILITY, NOW.date(), now=NOW)
    assert all(e.value["applicable"] is False and "ValueError" not in e.value["error"] for e in evidence)
    monkeypatch.setattr(airnow, "_fetch", lambda u: b"[]")
    assert all(e.value["error"] == "no monitor within 25 mi" for e in airnow.collect(FACILITY, NOW.date(), now=NOW))


def test_airnow_provider_failure_has_sanitized_message(monkeypatch):
    key = "fixture-secret-key"
    monkeypatch.setattr(airnow, "settings", replace(airnow.settings, airnow_api_key=key))
    monkeypatch.setattr(airnow, "_fetch", lambda u: json.dumps({"WebServiceError": [{"Message": f"Quota exceeded {key}"}]}).encode())
    rows = airnow.collect(FACILITY, NOW.date(), now=NOW)
    assert all("Quota exceeded" in e.value["error"] and key not in e.model_dump_json() for e in rows)


def test_no_nearby_gauge_is_not_unknown_failure(monkeypatch):
    seen = []
    def fetch(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(200, json={"gauges": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(nwps.httpx, "get", fetch)
    [ev] = nwps.collect_nearby(FACILITY, compute_windows(FACILITY, NOW, load_policy()))
    assert ev.value["applicable"] is False and ev.value["error"] == "no river gauge nearby"
    assert seen[0]["srid"] == "EPSG_4326"


def test_nearest_eligible_gauge_distance_categories_and_stage(monkeypatch):
    categories = {name: {"stage": number} for name, number in zip(("action", "minor", "moderate", "major"), (10, 12, 14, 16))}
    gauges = [{"lid": "NEAR1", "latitude": FACILITY.lat+.01, "longitude": FACILITY.lon},
              {"lid": "VALID1", "latitude": FACILITY.lat+.05, "longitude": FACILITY.lon}]
    fetched = []
    def get(url, **kwargs):
        fetched.append(url)
        if url == nwps.BASE:
            data = {"gauges": gauges}
        elif url.endswith("/stageflow"):
            data = {"observed": {"primaryUnits": "ft", "data": [{"validTime": NOW.isoformat(), "primary": 11}]},
                    "forecast": {"primaryUnits": "ft", "data": []}}
        else:
            lid = url.rsplit("/", 1)[-1]
            data = {"lid": lid, "name": "Test river", "flood": {"stageUnits": "ft", "categories": categories if lid == "VALID1" else {}}}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))
    monkeypatch.setattr(nwps.httpx, "get", get)
    rows = nwps.collect_nearby(FACILITY, compute_windows(FACILITY, NOW, load_policy()))
    assert all(r.value["gauge"] == "VALID1" and 0 < r.value["distance_km"] < 40 for r in rows)
    assert rows[0].value["categories"] == categories and rows[0].snapshot_hash
    assert rows[1].value["current"]["primary"] == 11
    assert rows[1].status == "ok" and "observation" in rows[1].value["forecast_note"]


def test_emergency_reminder_and_escalation_survive_flag_off(demo_db, monkeypatch):
    rec = storm(demo_db)
    disable_staffing(monkeypatch)
    decision = services.record_decision(demo_db, rec, schemas.DecisionIn(action="approve", manager_name="Maria"))
    notice = demo_db.scalar(select(models.Notice).where(models.Notice.decision_id == decision.id))
    dana = demo_db.scalar(select(models.Notification).where(models.Notification.notice_id == notice.id,
        models.Notification.employee_id == seed.emp_id("ava"), models.Notification.message_type == "action_required"))
    dana.sent_at = datetime.now(timezone.utc)
    demo_db.commit()
    delivered = []
    monkeypatch.setattr(escalation, "deliver", lambda p, channels, *a, **k: delivered.append((p.id, channels)) or {})
    events = escalation.tick(dana.sent_at+timedelta(minutes=2, seconds=1))
    assert "ack.reminder_sent" in events and "ack.escalated.1" in events
    assert (dana.employee_id, ["push"]) in delivered
    assert any(person != dana.employee_id for person, _ in delivered)

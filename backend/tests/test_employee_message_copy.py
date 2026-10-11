"""Employee copy is a view of computed duties, not another people engine."""
from dataclasses import replace
from datetime import date

import pytest

from app.access.policy import Access
from app.notify import messages, notices


def facts(**changes):
    base = messages.MessageFacts(
        name="Dana R.", language="en", facility_name="USDA demo office, Brantley GA",
        operating_date=date(2026, 10, 13), posture="closure", delay_hours=2,
        message_type="action_required", change="initial", instruction="report as normal and acknowledge",
        access=Access("allowed", None, "ACCESS_EMERGENCY", "emergency staff"),
        why_codes=["EMERGENCY_DUTY"], requires_ack=True, ack_url="https://demo.invalid/ack/n1",
        hazard="Flood Warning", hazard_source="NWS", hazard_end="4:30 AM EDT (Wed Oct 14)",
        footer="Demo: synthetic office, real NWS alert", report_time="05:00", function_name="animal care")
    return replace(base, **changes)


@pytest.mark.parametrize("hazard", ["Flood Warning", "Flash Flood Warning", "Winter Storm Warning",
                                   "Winter Weather Advisory", "Ice Storm Warning", "Severe Thunderstorm Warning",
                                   "Tornado Warning", "High Wind Warning"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_named_hazard_personal_action_five_lines_and_short_subject(hazard, language):
    f = facts(hazard=hazard, language=language)
    subject, body = messages.template(f)
    lines = body.splitlines()
    assert len(lines) == 5 and len(subject.split()) < 10
    assert "NWS" in lines[0] and "4:30 AM EDT" in lines[0] and "Brantley GA" in lines[0]
    assert lines[1].startswith("**") and "05:00" in lines[1]
    assert ("emergency staff" if language == "en" else "personal de emergencia") in lines[1]
    assert f.ack_url in lines[3]
    assert not any(term in body.lower() for term in ("home-zone", "source coverage", "not available for this location"))
    assert messages.validate(body, f) is None


@pytest.mark.parametrize("posture", ["telework", "closure"])
def test_telework_action_is_not_contradictory_and_fyi_has_no_ack(posture):
    f = facts(posture=posture, instruction="office closed, telework" if posture == "closure" else "telework today",
              why_codes=["SCHEDULED_ONSITE"], requires_ack=False, access=Access("denied", None, "", ""))
    subject, body = messages.template(f)
    assert "**Don't come in; work from home from 05:00.**" in body
    assert "No reply needed." in body and f.ack_url not in body
    assert "work remotely" in subject and "report" not in subject


def test_facility_ops_and_call_in_name_the_exception():
    ops = facts(why_codes=["FACILITY_OPS", "EMERGENCY_DUTY"],
                instruction="report as normal and acknowledge; secure the building and post closure notices")
    assert "facility operations duty" in messages.template(ops)[1]
    backup = facts(message_type="call_in", why_codes=["CALL_IN"], function_name="animal care")
    assert "manager called you in for animal care; report on site by 05:00" in messages.template(backup)[1]


def test_plain_email_and_html_preserve_same_five_lines(demo_email_capture):
    person, captured = demo_email_capture
    subject, body = messages.template(facts())
    notices.deliver(person, ["email"], subject, body)
    payload = captured[0]
    assert payload["text"] == body and len(payload["text"].splitlines()) == 5
    assert len(payload["subject"].split()) < 10 and "<strong>" in payload["html"]


@pytest.fixture
def demo_email_capture(monkeypatch):
    import httpx
    from app import models
    from app.notify import channels
    from app.seed import emp_id
    configured = replace(channels.settings, demo_mode=True, demo_email="owner@example.invalid",
                         resend_api_key="fixture-key", resend_from="Demo <demo@example.invalid>")
    monkeypatch.setattr(channels, "settings", configured)
    monkeypatch.setattr(notices, "settings", configured)
    captured = []
    def post(url, **kw):
        captured.append(kw["json"])
        return httpx.Response(200, json={"id": "mock-email"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    person = models.Employee(id=emp_id("ava"), name="Dana R.", role="employee", accommodation_needs=[])
    return person, captured


def test_reminder_push_failure_cannot_fall_back_to_email(monkeypatch):
    from app import models
    monkeypatch.setattr(notices, "settings", replace(notices.settings, demo_mode=False))
    monkeypatch.setattr(notices, "send_push", lambda *args: (False, "provider failure"))
    monkeypatch.setattr(notices, "send_email", lambda *args, **kw: pytest.fail("reminder email attempted"))
    person = models.Employee(id="test", name="Test", ntfy_topic="test", accommodation_needs=[])
    results = notices.deliver(person, ["push"], "Warning", "Confirm receipt", allow_email_fallback=False)
    assert list(results) == ["push"] and not results["push"]["ok"]

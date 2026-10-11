"""Mocked provider delivery: demo staff never look like real deliveries or failures."""
from dataclasses import replace

import httpx
import pytest

from app import models
from app.notify import channels, notices
from app.seed import emp_id


@pytest.fixture
def demo(monkeypatch):
    configured = replace(channels.settings, demo_mode=True, demo_email="owner@test.invalid",
                         resend_api_key="test-provider-secret", resend_from="Demo <demo@test.invalid>",
                         notify_email_override="wrong@test.invalid", ntfy_topic="global-not-for-synthetic")
    monkeypatch.setattr(channels, "settings", configured)
    monkeypatch.setattr(notices, "settings", configured)
    return configured


def person(key="sam", topic="demo-persona"):
    return models.Employee(id=emp_id(key), name="Sam Okafor", role="employee", email="synthetic@example.com",
                           ntfy_topic=topic, accommodation_needs=[])


def test_synthetic_channels_are_simulated_without_network(demo, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("synthetic delivery used network"))
    synthetic = person("carla", topic=None)
    results = notices.deliver(synthetic, ["push", "email"], "SIMULATED closure", "SIMULATED instructions")
    assert all(r["status"] == "simulated" and not r["ok"] and r["sent_at"] is None and r["error"] is None
               for r in results.values())
    assert results["push"]["message"] == "simulated: synthetic employee, no device"
    assert results["email"]["message"] == "simulated: synthetic employee"


def test_persona_email_redirect_and_addressed_to_prefix(demo, monkeypatch):
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"id": "provider-id"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    result = notices.deliver(person(), ["email"], "SIMULATED closure", "SIMULATED instructions")["email"]
    payload = captured[0]
    assert payload["to"] == [demo.demo_email]
    assert payload["from"] == demo.resend_from
    assert payload["subject"] == "[Demo -> Sam Okafor] SIMULATED closure"
    assert payload["text"].startswith("This demo message was addressed to Sam Okafor (employee).\n")
    assert "SIMULATED" in payload["text"]
    assert result["ok"] and result["status"] == "delivered" and result["sent_at"]


@pytest.mark.parametrize("channel,status,message", [("email", 403, "Only the account owner may receive test email"),
                                                    ("push", 429, "Rate limit exceeded")])
def test_provider_failures_keep_http_status_and_message(demo, monkeypatch, channel, status, message):
    def post(url, **kwargs):
        return httpx.Response(status, json={"message": message + " " + demo.resend_api_key},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    results = notices.deliver(person(), [channel], "SIMULATED closure", "SIMULATED instructions")
    result = results[channel]
    assert result["status"] == "failed" and result["http_status"] == status
    assert message in result["error"] and demo.resend_api_key not in result["error"]
    assert result["sent_at"] is None


def test_missing_demo_email_never_sends_to_synthetic_address(demo, monkeypatch):
    monkeypatch.setattr(notices, "settings", replace(demo, demo_email=""))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("unsafe email attempted"))
    result = notices.deliver(person(), ["email"], "subject", "body")["email"]
    assert result["status"] == "failed" and result["error"] == "DEMO_EMAIL is not configured"


def test_non_demo_missing_topic_remains_failure(monkeypatch):
    monkeypatch.setattr(channels, "settings", replace(channels.settings, demo_mode=False, ntfy_topic=""))
    monkeypatch.setattr(notices, "settings", replace(notices.settings, demo_mode=False))
    monkeypatch.setattr(notices, "send_email", lambda *a, **k: (False, "not configured"))
    result = notices.deliver(person("carla", None), ["push"], "subject", "body")["push"]
    assert result["status"] == "failed" and result["error"] == "no ntfy topic for this person"

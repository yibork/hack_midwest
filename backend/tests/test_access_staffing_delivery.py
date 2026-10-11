"""Access policy per posture, staffing shortfall and backup ranking, notify_by / waves / channels /
decision clock, push-failure email fallback, and message validation. No network."""
from datetime import timedelta

import pytest

from app.access.policy import access_for
from app.notify import channels, delivery, messages, notices
from app.notify.impact import Exposure, ZoneAlert
from app.schemas import CriticalFunction
from app.staffing.coverage import CallIn, coverage
from tests.test_impact import at, here, impact, person, shift

CRIT = {"animal_care", "it_ops"}


# ---------------------------------------------------------------- access policy

@pytest.mark.parametrize("posture,level,expected", [
    ("normal", "standard", "allowed"), ("normal", "contractor_badge", "allowed"),
    ("normal", "escort_required", "allowed_with_escort"),
    ("delayed_arrival", "emergency", "allowed"), ("delayed_arrival", "facility_ops", "allowed"),
    ("delayed_arrival", "standard", "allowed_after 09:30"), ("delayed_arrival", "contractor_badge", "allowed_after 09:30"),
    ("early_dismissal", "standard", "allowed_until 14:00"), ("early_dismissal", "emergency", "allowed"),
    ("early_dismissal", "facility_ops", "allowed"),
    ("telework", "standard", "allowed"), ("telework", "contractor_badge", "allowed"),
    ("closure", "emergency", "allowed"), ("closure", "facility_ops", "allowed"), ("closure", "standard", "denied"),
    ("closure", "contractor_badge", "denied"), ("closure", "escort_required", "denied"),
])
def test_access_by_posture(posture, level, expected):
    a = access_for(level, posture, report_time="09:30", dismissal_time="14:00", critical_codes=CRIT)
    assert a.label == expected and a.code.startswith("ACCESS_") and a.reason


def test_contractor_in_critical_function_allowed_on_closure():
    a = access_for("contractor_badge", "closure", functions=["it_ops"], critical_codes=CRIT)
    assert a.label == "allowed" and a.code == "ACCESS_CONTRACTOR_CRITICAL"


# ---------------------------------------------------------------- staffing

ANIMAL = CriticalFunction(facility_id="f", code="animal_care", name="Animal care", min_onsite={"closure": 2, "normal": 1})


def _staff():
    def mk(pid, name, designation="non_telework", status="onsite", commute=30, exposure=Exposure(), home="MOZ037"):
        p = person(id=pid, name=name, designation=designation, functions=["animal_care"], commute_minutes=commute,
                   home_zone=home)
        return impact("closure", p=p, shifts=[shift(status, pid=pid)], assignments=here(pid), exposure=exposure)
    warn = Exposure(alerts=(ZoneAlert("MOZ043", "Winter Storm Warning", None, None),))
    return [
        mk("a", "Ava Brennan", "emergency"),                       # available
        mk("t", "Tomás Quiroga", "emergency", status="leave"),     # on leave: not available, not a backup
        mk("j", "Jordan Pike", "telework_ready", commute=15),      # backup (closure: not onsite)
        mk("r", "Ray Long", "telework_ready", commute=50),         # backup, longer commute
        mk("x", "Xena Exposed", "telework_ready", commute=5, exposure=warn, home="MOZ043"),  # exposed: never a backup
        mk("e", "Emil Exposed", "emergency", exposure=warn, home="MOZ043"),  # emergency but exposed: flagged
    ]


def test_staffing_shortfall_with_ranked_backups():
    [item] = coverage([ANIMAL], _staff(), "closure")
    assert item.label == "Animal care 1/2 - shortfall" and item.reason_code == "STAFFING_SHORTFALL" and item.gap == 1
    assert [b.name for b in item.backups] == ["Jordan Pike", "Ray Long"]  # shortest commute first
    assert item.exposed == ["Xena Exposed", "Emil Exposed"]


def test_call_ins_pending_then_covered():
    staff = _staff()
    [pending] = coverage([ANIMAL], staff, "closure", [CallIn("n1", "j", "animal_care", False)])
    assert pending.status == "pending" and [c.name for c in pending.called_in] == ["Jordan Pike"]
    [covered] = coverage([ANIMAL], staff, "closure", [CallIn("n1", "j", "animal_care", True)])
    assert covered.label == "Animal care 2/2 - covered" and covered.backups == []


# ---------------------------------------------------------------- notify_by, waves, channels, clock

def test_notify_by_delay_and_closure_use_shift_commute_and_30():
    i = impact("delayed_arrival", p=person(commute_minutes=40), shifts=[shift(start="05:00", end="13:30")])
    assert i.notify_by == at("05:00") - timedelta(minutes=70)


def test_notify_by_early_dismissal_is_dismissal_minus_45():
    assert impact("early_dismissal").notify_by == at("14:00") - timedelta(minutes=45)


def test_decision_clock_names_the_person_driving_it():
    dana = impact("closure", p=person(id="d", name="Dana Reyes", commute_minutes=40), shifts=[shift(start="05:00", end="13:30", pid="d")],
                  assignments=here("d"))
    sam = impact("closure", p=person(id="s", name="Sam Whitaker", commute_minutes=25), shifts=[shift(pid="s")],
                 assignments=here("s"))
    clock = delivery.decision_clock([sam, dana])
    assert clock.employee_id == "d" and clock.at == at("03:50")
    assert clock.label == "first: Dana R., early shift 05:00, 40 min commute"


def test_waves():
    now = at("03:00")
    assert delivery.wave_for("action_required", wave1=True, notify_by=at("09:00"), now=now) == "1"
    assert delivery.wave_for("action_required", wave1=False, notify_by=at("03:45"), now=now) == "1"  # due within 60 min
    assert delivery.wave_for("action_required", wave1=False, notify_by=at("06:00"), now=now) == "2"
    assert delivery.wave_for("call_in", wave1=False, notify_by=None, now=now) == "1"
    assert delivery.wave_for("fyi", wave1=True, notify_by=None, now=now) == "fyi"


def test_channels():
    p = person(preferred_channels=["email", "push"])
    assert delivery.channels_for(p, "action_required", at("05:30")) == ["push", "email"]  # before 06:00
    assert delivery.channels_for(p, "action_required", at("08:00")) == ["email"]  # preference
    assert delivery.channels_for(person(), "fyi", at("05:30")) == ["email"]  # FYI: email only


def test_push_failure_falls_back_to_email(monkeypatch):
    sent = []
    monkeypatch.setattr(notices, "send_push", lambda *a, **k: (False, "ntfy error: ConnectError"))
    monkeypatch.setattr(notices, "send_email", lambda to, s, b, h=None: (sent.append(to) or (True, None)))
    emp = type("E", (), {"ntfy_topic": "t", "email": "a@example.com", "accommodation_needs": []})()
    results = notices.deliver(emp, ["push"], "subj", "body")
    assert results["push"]["ok"] is False and results["push"]["error"]
    assert results["email"]["ok"] is True and results["email"]["fallback"] is True and sent == ["a@example.com"]


def test_unconfigured_push_reports_error(monkeypatch):
    import dataclasses
    monkeypatch.setattr(channels, "settings", dataclasses.replace(channels.settings, ntfy_topic=""))
    ok, err = channels.send_push(None, "t", "m")
    assert ok is False and "topic" in err


# ---------------------------------------------------------------- message validation and rendering

def _facts(**kw):
    i = impact("delayed_arrival", p=person(**kw.pop("p", {})))
    base = dict(name="Dana Reyes", language="en", facility_name="KC Office", operating_date=at("00:00").date(),
                posture="delayed_arrival", delay_hours=2, message_type="action_required", change="initial",
                instruction=i.duty_instruction, access=i.access, why_codes=i.codes, requires_ack=True,
                ack_url="http://x/ack/n1", render=i.render)
    base.update(kw)
    return messages.MessageFacts(**base)


def test_template_has_every_part_and_validates():
    f = _facts()
    subject, body = messages.template(f)
    assert messages.validate(body, f) is None
    for part in ("delayed arrival", "**Report by 09:30 or telework.**", "Building access",
                 "http://x/ack/n1"):
        assert part in body


def test_validator_rejects_missing_instruction_wrong_time_and_emoji():
    f = _facts()
    assert "instruction" in messages.validate("Dana: please come in at some point. http://x/ack/n1", f)
    assert "times" in messages.validate(messages.template(f)[1] + "\nMaybe 10:15.", f)
    assert "emoji" in messages.validate("Dana: report by 09:30 or telework ❄ http://x/ack/n1", f)
    assert "acknowledgment" in messages.validate(messages.template(f)[1].replace(f.ack_url, ""), f)


def test_spanish_and_plain_language_rendering():
    i = impact("delayed_arrival", p=person(language="es", accommodation_needs=["plain_language"]))
    f = _facts(language="es", instruction=i.duty_instruction, render=i.render)
    _, body = messages.template(f)
    assert "Preséntese a más tardar a las 09:30" in body and "llegada retrasada" in body
    assert messages.validate(body, f) is None


def test_large_text_email_html():
    assert "font-size:22px" in messages.html_body("a\nb", frozenset({"large_text"}))
    assert messages.html_body("a", frozenset()) is None

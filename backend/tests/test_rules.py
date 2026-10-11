"""Rules engine tests. Pure: no network, no database."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.rules.engine import evaluate, load_policy
from app.schemas import Evidence, Facility

CT = ZoneInfo("America/Chicago")
FACILITY = Facility(id="f1", name="Test Office", lat=39.1, lon=-94.6, timezone="America/Chicago",
                    open_time="07:30", close_time="16:00", access_routes=["I-35"])
# 05:00 local on a weekday: before the 05:30-07:30 commute window.
NOW = datetime(2026, 10, 12, 5, 0, tzinfo=CT).astimezone(timezone.utc)


def local(h, m=0, day=12):
    return datetime(2026, 10, day, h, m, tzinfo=CT)


def alert(event, onset, ends, severity="Severe", urgency="Expected", certainty="Likely"):
    return {"id": event, "event": event, "severity": severity, "urgency": urgency, "certainty": certainty,
            "onset": onset.isoformat(), "ends": ends.isoformat() if ends else None,
            "headline": event, "instruction": None, "areaDesc": "Jackson, MO", "sent": NOW.isoformat()}


def alerts_ev(*alerts, status="ok"):
    return Evidence(id="ev-alerts", source="nws", kind="active_alerts", status=status,
                    value={"count": len(alerts), "alerts": list(alerts)},
                    retrieved_at=NOW, valid_until=NOW + timedelta(minutes=15))


def wind_ev(gusts: dict[int, float] | None = None, default=15.0, status="ok"):
    """Hourly gusts from 05:00 to 23:00 local; overrides by local hour."""
    gusts = gusts or {}
    hours = [{"time": local(h).astimezone(timezone.utc).isoformat(), "wind_mph": 10.0,
              "gust_mph": gusts.get(h, default)} for h in range(5, 24)]
    return Evidence(id="ev-wind", source="nws", kind="wind_forecast", status=status, units="mph",
                    value={"hours": hours}, retrieved_at=NOW, valid_until=NOW + timedelta(hours=3))


def run(*evidence, now=NOW):
    return evaluate(FACILITY, list(evidence), now)


def fired(rec):
    return {t.rule_id for t in rec.triggers}


def test_calm_is_normal_high_confidence():
    rec = run(alerts_ev(), wind_ev())
    assert rec.posture == "normal"
    assert rec.triggers == []
    assert rec.confidence == "high"
    assert any("58 mph" in c and "closure" in c and "15 mph" in c for c in rec.what_would_change)
    assert rec.policy_version.startswith("1.0.1+")


def test_extreme_immediate_alert_closes():
    a = alert("Blizzard Warning", local(6), local(18), severity="Extreme", urgency="Immediate")
    rec = run(alerts_ev(a), wind_ev())
    assert rec.posture == "closure"
    assert "extreme_immediate_alert" in fired(rec)


def test_extreme_alert_outside_operating_hours_does_not_fire():
    a = alert("Blizzard Warning", local(20), local(23), severity="Extreme", urgency="Immediate")
    rec = run(alerts_ev(a), wind_ev())
    assert "extreme_immediate_alert" not in fired(rec)


def test_high_wind_warning_closes():
    rec = run(alerts_ev(alert("High Wind Warning", local(4), local(12))), wind_ev())
    assert rec.posture == "closure"
    assert fired(rec) == {"high_wind"}


def test_gusts_58_in_commute_window_close():
    rec = run(alerts_ev(), wind_ev({6: 61.0}))
    assert rec.posture == "closure"
    t = next(t for t in rec.triggers if t.rule_id == "high_wind")
    assert (t.threshold, t.observed, t.margin, t.units) == (58, 61.0, 3.0, "mph")


def test_gusts_outside_commute_window_ignored():
    rec = run(alerts_ev(), wind_ev({12: 70.0}))
    assert rec.posture == "normal"


def test_wind_advisory_delays():
    rec = run(alerts_ev(alert("Wind Advisory", local(5), local(11))), wind_ev())
    assert rec.posture == "delayed_arrival"
    assert fired(rec) == {"wind_advisory"}


@pytest.mark.parametrize("gust,expected", [(45.9, "normal"), (46, "delayed_arrival"),
                                           (57.9, "delayed_arrival"), (58, "closure")])
def test_gust_band_boundaries(gust, expected):
    assert run(alerts_ev(), wind_ev({7: gust})).posture == expected


def test_advisory_band_what_would_change_points_to_closure():
    rec = run(alerts_ev(), wind_ev({7: 50.0}))
    assert rec.what_would_change[0].startswith("Gusts reaching 58 mph")
    assert "forecast peak is 50 mph" in rec.what_would_change[0]


@pytest.mark.parametrize("event", ["Flood Warning", "Flash Flood Warning"])
def test_flood_warning_telework(event):
    rec = run(alerts_ev(alert(event, local(3), local(20))), wind_ev())
    assert rec.posture == "telework"
    assert fired(rec) == {"flood"}


def test_storm_starting_after_open_is_early_dismissal():
    rec = run(alerts_ev(alert("Severe Thunderstorm Warning", local(13), local(14))), wind_ev())
    assert rec.posture == "early_dismissal"


def test_tornado_starting_before_open_is_delayed_arrival():
    rec = run(alerts_ev(alert("Tornado Warning", local(7), local(8), certainty="Observed")), wind_ev())
    assert rec.posture == "delayed_arrival"
    assert fired(rec) == {"convective"}


def test_storm_after_close_does_not_fire():
    rec = run(alerts_ev(alert("Severe Thunderstorm Warning", local(17), local(18))), wind_ev())
    assert rec.posture == "normal"


# ---------------------------------------------------------------- most restrictive wins

@pytest.mark.parametrize("alerts,gust,expected", [
    # closure (gusts) beats telework (flood)
    ([alert("Flood Warning", local(3), local(20))], 60.0, "closure"),
    # telework (flood) beats early_dismissal (storm after open)
    ([alert("Flood Warning", local(3), local(20)),
      alert("Severe Thunderstorm Warning", local(13), local(14))], 15.0, "telework"),
    # early_dismissal beats delayed_arrival (advisory gusts)
    ([alert("Severe Thunderstorm Warning", local(13), local(14))], 50.0, "early_dismissal"),
    # delayed_arrival beats normal
    ([], 50.0, "delayed_arrival"),
])
def test_most_restrictive_wins(alerts, gust, expected):
    rec = run(alerts_ev(*alerts), wind_ev({6: gust}))
    assert rec.posture == expected
    assert len(rec.triggers) >= 1


def test_every_fired_rule_is_reported():
    rec = run(alerts_ev(alert("Flood Warning", local(3), local(20)),
                        alert("Wind Advisory", local(5), local(11))), wind_ev({6: 60}))
    assert fired(rec) == {"flood", "wind_advisory", "high_wind"}
    assert rec.posture == "closure"


# ---------------------------------------------------------------- confidence

def test_unknown_evidence_lowers_confidence_and_is_not_safe():
    bad = Evidence(id="ev-alerts", source="nws", kind="active_alerts", status="unknown",
                   value={"error": "timeout"}, retrieved_at=NOW)
    rec = run(bad, wind_ev())
    assert rec.posture == "normal"
    assert rec.confidence == "low"
    assert any("not treated as safe" in r for r in rec.confidence_reasons)
    assert "could not be retrieved" in rec.what_would_change[0]


def test_missing_wind_evidence_is_low_confidence():
    assert run(alerts_ev()).confidence == "low"


def test_stale_evidence_is_medium():
    old = NOW - timedelta(hours=5)
    ev = wind_ev()
    ev.retrieved_at, ev.valid_until = old, old + timedelta(hours=3)
    assert run(alerts_ev(), ev).confidence == "medium"


def test_possible_certainty_lowers_confidence():
    a = alert("Flood Warning", local(3), local(20), certainty="Possible")
    rec = run(alerts_ev(a), wind_ev())
    assert rec.confidence == "medium"
    assert any("Possible" in r for r in rec.confidence_reasons)


def test_near_threshold_is_medium():
    assert run(alerts_ev(), wind_ev({6: 55.0})).confidence == "medium"


def test_operating_date_rolls_after_close():
    after_close = local(17).astimezone(timezone.utc)
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev()], after_close)
    assert rec.operating_date.isoformat() == "2026-10-13"


def test_policy_loads_all_starter_rules():
    ids = [r["id"] for r in load_policy().rules]
    assert ids == ["extreme_immediate_alert", "high_wind", "wind_advisory", "flood", "convective"]
    assert all(r["verified"] and r["citation"]["url"] and r["justification"] for r in load_policy().rules)

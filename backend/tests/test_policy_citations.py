from pathlib import Path

import pytest
import yaml

from app.rules.engine import POLICY_PATH, load_policy, policy_catalog
from app.services import change_rule_id
from tests.test_rules import FACILITY, NOW, alerts_ev, wind_ev
from app.rules.engine import evaluate


def test_every_policy_entry_has_complete_provenance():
    catalog = policy_catalog()
    assert catalog["version"].startswith("1.0.1+")
    assert catalog["change_log"]
    for rule in catalog["rules"]:
        assert rule["justification"] and rule["verified"] is True
        assert set(rule["citation"]) == {"title", "publisher", "url", "section_or_page", "retrieved_at"}
        assert all(rule["citation"].values())


@pytest.mark.parametrize("section", ["rules", "references"])
def test_release_refuses_unverified_entry(tmp_path, section):
    data = yaml.safe_load(POLICY_PATH.read_text())
    data[section][0]["verified"] = False
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="non-draft"):
        load_policy(path)
    data["version"] = "1.0.0-draft"
    path.write_text(yaml.safe_dump(data))
    assert load_policy(path).version.startswith("1.0.0-draft")


def test_release_refuses_missing_citation(tmp_path):
    data = yaml.safe_load(POLICY_PATH.read_text())
    data["rules"][0]["citation"].pop("url")
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="citation"):
        load_policy(path)


@pytest.mark.parametrize("speed,posture", [(30.9, "normal"), (31, "delayed_arrival"), (39.9, "delayed_arrival"), (40, "closure")])
def test_eax_sustained_wind_boundary(speed, posture):
    wind = wind_ev()
    for hour in wind.value["hours"]:
        hour["wind_mph"] = speed
    assert evaluate(FACILITY, [alerts_ev(), wind], NOW).posture == posture


def test_every_change_and_trigger_resolves_to_catalog():
    catalog = {r["id"] for r in policy_catalog()["rules"]}
    rec = evaluate(FACILITY, [alerts_ev(), wind_ev({6: 60})], NOW)
    assert all(t.rule_id in catalog for t in rec.triggers)
    assert all(change_rule_id(line) in catalog for line in rec.what_would_change)


def test_verified_kcdm7_stages():
    row = next(r for r in policy_catalog()["rules"] if r["id"] == "river_kcdm7")
    assert row["values"] == {"action": 29, "minor": 32, "moderate": 35, "major": 49}

from datetime import date, datetime, timezone

import pytest

from app import calendar, demo, models, schemas, seed, services, memo
from app.notify import notices, messages
from app.agents.writer_agent import build_facts
from tests.test_demo_scenarios import demo_db


@pytest.mark.parametrize("day,weekday", [(11, "Sunday"), (12, "Monday"), (13, "Tuesday")])
def test_demo_workday_uses_target_weekday(day, weekday):
    assert calendar.demo_workday_note(date(2026, 10, day)) == f"{weekday} is treated as a workday for this demonstration."


def test_old_rationale_memo_and_new_messages_use_same_copy_without_rewriting_record(demo_db, monkeypatch):
    monkeypatch.setattr(demo, "utcnow", lambda: datetime(2026, 10, 11, 12, tzinfo=timezone.utc))
    rec = demo.synthetic(demo_db, demo_db.get(models.Facility, seed.FACILITY_ID))
    old = "DEMO: today (Sun Oct 11) evaluated as a workday; really, sun Oct 11 is not an operating day."
    rec.target_note = old
    rec.rationale = "Target day: " + old + " NWS wind evidence applies."
    demo_db.commit()
    expected = "Sunday is treated as a workday for this demonstration."
    detail = services.recommendation_detail(demo_db, rec)
    assert expected in detail.rationale and detail.target_note == expected
    assert "really" not in detail.rationale and "not an operating day" not in detail.rationale
    assert rec.target_note == old  # historical calendar record remains intact
    facts = build_facts(detail, [], "Demo office", "America/Chicago")
    assert facts["target_note"] == expected and "really" not in facts["deterministic_summary"]
    decision = services.record_decision(demo_db, rec, schemas.DecisionIn(action="approve", manager_name="Maria"))
    page = memo.render(demo_db, decision)
    assert expected in page and "really" not in page and old not in page
    plan = notices.plan_for(demo_db, demo_db.get(models.Facility, rec.facility_id), rec, rec.posture)
    for p in plan.planned:
        if not p.facts:
            continue
        _, body = messages.template(p.facts)
        phrase = calendar.demo_workday_note(rec.operating_date, p.facts.lang)
        assert phrase in body and "really" not in body
        assert len(body.splitlines()) <= 5
        assert messages.validate(body, p.facts) is None

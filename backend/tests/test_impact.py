"""Personal impact engine: one test per reason code, shift-overlap cases, exposure, privacy of
accommodation codes. Pure: no database, no network."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.notify.impact import Exposure, PostureContext, ZoneAlert, compute_impact, public_codes
from app.schemas import Assignment, Employee, Shift

TZ = ZoneInfo("America/Chicago")
DAY = date(2026, 1, 14)  # a Wednesday
FAC = "usda-kc-demo"
NOW = datetime(2026, 1, 14, 3, 0, tzinfo=TZ).astimezone(timezone.utc)


def person(**kw) -> Employee:
    base = dict(id="p1", name="Dana Reyes", role="employee", designation="telework_ready", functions=[],
                language="en", preferred_channels=["push", "email"], email="d@example.com", home_zone="MOZ037",
                home_county="MOC095", commute_minutes=40, commute_routes=["I-35"], access_level="standard")
    base.update(kw)
    return Employee(**base)


def shift(status="onsite", start="07:30", end="16:00", day=DAY, pid="p1") -> Shift:
    return Shift(employee_id=pid, date=day, status=status, shift_start=start if status == "onsite" else None,
                 shift_end=end if status == "onsite" else None)


def here(pid="p1") -> list[Assignment]:
    return [Assignment(employee_id=pid, facility_id=FAC, kind="permanent")]


def ctx(posture="closure", **kw) -> PostureContext:
    base = dict(facility_id=FAC, facility_name="KC Office", timezone="America/Chicago", operating_date=DAY,
                posture=posture, open_time="07:30", close_time="16:00", now=NOW,
                critical_codes=frozenset({"animal_care", "it_ops", "security", "lab_freezer_monitoring"}))
    base.update(kw)
    return PostureContext(**base)


def impact(posture="closure", p=None, shifts=None, assignments=None, exposure=Exposure(), **ckw):
    p = p or person()
    return compute_impact(ctx(posture, **ckw), p, assignments if assignments is not None else here(p.id),
                          shifts if shifts is not None else [shift(pid=p.id)], exposure)


def at(hm: str, day: date = DAY) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


# ---------------------------------------------------------------- 1. assignment

def test_detail_out_is_excluded_with_destination():
    a = here() + [Assignment(employee_id="p1", facility_id="usda-stl-demo", kind="detail_out",
                             start_date=DAY - timedelta(days=2), end_date=DAY + timedelta(days=5))]
    i = compute_impact(ctx(), person(), a, [shift()], Exposure(), {"usda-stl-demo": "St. Louis Office"})
    assert i.codes == ["DETAIL_OUT"] and i.message_type == "none"
    assert "St. Louis Office" in i.reason_text and (DAY + timedelta(days=5)).isoformat() in i.reason_text


def test_detail_in_is_treated_as_local():
    a = [Assignment(employee_id="p1", facility_id="usda-stl-demo", kind="permanent"),
         Assignment(employee_id="p1", facility_id=FAC, kind="detail_in", end_date=DAY + timedelta(days=3))]
    i = impact(assignments=a)
    assert "DETAIL_IN" in i.codes and i.message_type == "action_required"
    assert i.duty_instruction == "office closed, telework"


# ---------------------------------------------------------------- 2. schedule

@pytest.mark.parametrize("status,code", [("leave", "LEAVE"), ("day_off", "DAY_OFF")])
def test_leave_and_day_off_excluded_fyi_only_on_closure(status, code):
    closure = impact("closure", shifts=[shift(status)])
    delay = impact("delayed_arrival", shifts=[shift(status)])
    assert closure.codes == [code] and closure.message_type == "fyi"
    assert delay.codes == [code] and delay.message_type == "none"


def test_telework_scheduled_gets_fyi_no_change():
    i = impact("closure", shifts=[shift("telework")])
    assert i.codes == ["TELEWORK_SCHEDULED"] and i.message_type == "fyi"
    assert i.duty_instruction == "no change for you: keep teleworking"
    assert impact("normal", shifts=[shift("telework")]).message_type == "none"


# ---------------------------------------------------------------- 3. shift overlap

def test_early_dismissal_after_shift_end_not_affected():
    i = impact("early_dismissal", shifts=[shift(start="07:30", end="13:00")])  # dismissal 14:00
    assert "SHIFT_NOT_AFFECTED" in i.codes and i.message_type == "none"


def test_delay_does_not_affect_night_shift():
    i = impact("delayed_arrival", shifts=[shift(start="22:00", end="06:00")])
    assert "SHIFT_NOT_AFFECTED" in i.codes and i.message_type == "none"


def test_night_shift_that_started_yesterday_is_evaluated_for_today():
    yesterday = shift(start="22:00", end="06:00", day=DAY - timedelta(days=1))
    i = impact("closure", p=person(designation="non_telework"), shifts=[yesterday, shift("day_off")])
    assert i.message_type == "action_required" and "SHIFT_NOT_AFFECTED" not in i.codes
    assert i.shift.starts_previous_day
    assert i.notify_by == at("06:00") - timedelta(minutes=45)  # before they head home


def test_delay_affects_early_shift_with_personal_report_time():
    i = impact("delayed_arrival", p=person(designation="non_telework"), shifts=[shift(start="05:00", end="13:30")])
    assert i.report_time == "07:00" and i.duty_instruction == "report by 07:00"


# ---------------------------------------------------------------- 4. designation / role

def test_emergency_duty_wave1_ack():
    i = impact("closure", p=person(designation="emergency", access_level="emergency"))
    assert "EMERGENCY_DUTY" in i.codes and i.requires_ack and i.wave1
    assert i.duty_instruction == "report as normal and acknowledge" and i.access.status == "allowed"


def test_emergency_always_included_even_outside_window():
    i = impact("delayed_arrival", p=person(designation="emergency"), shifts=[shift(start="22:00", end="06:00")])
    assert i.message_type == "action_required" and "SHIFT_NOT_AFFECTED" not in i.codes


def test_facility_ops_building_actions():
    i = impact("closure", p=person(role="facility_ops", designation="emergency", access_level="facility_ops"))
    assert {"FACILITY_OPS", "EMERGENCY_DUTY"} <= set(i.codes) and i.requires_ack and i.wave1
    assert "secure the building" in i.duty_instruction


def test_contractor_gets_building_status_only():
    i = impact("closure", p=person(role="contractor", designation="non_telework", access_level="contractor_badge"))
    assert "CONTRACTOR_STATUS_ONLY" in i.codes and i.duty_instruction == "building closed to you today"
    assert "SCHEDULED_ONSITE" not in i.codes


def test_supervisor_summary_and_security_codes():
    assert "SUPERVISOR_SUMMARY" in impact(p=person(role="supervisor")).codes
    assert "SECURITY_ACCESS_LIST" in impact(p=person(role="security_desk", designation="emergency")).codes


def test_scheduled_onsite_default():
    i = impact("delayed_arrival")
    assert "SCHEDULED_ONSITE" in i.codes and i.duty_instruction == "report by 09:30 or telework"


# ---------------------------------------------------------------- 5. exposure

WARN = Exposure(alerts=(ZoneAlert("MOZ037", "Winter Storm Warning", at("00:00"), at("12:00")),))


def test_home_in_warning_area_on_normal_day_informs_supervisor():
    i = impact("normal", exposure=WARN)
    assert "HOME_IN_WARNING_AREA" in i.codes and i.exposed
    assert i.message_type == "fyi" and "supervisor" in i.duty_instruction  # not told to stay home
    assert i.supervisor_suggestion == "consider offering unscheduled telework/leave per agency policy"
    assert i.audience_text and "MOZ037" not in i.audience_text  # home area stays manager-only


def test_route_closed():
    i = impact("normal", exposure=Exposure(closed_routes=frozenset({"I-35"})))
    assert "ROUTE_CLOSED" in i.codes and i.exposed and "I-35" in i.reason_text


def test_exposed_emergency_is_flagged_not_suggested():
    i = impact("normal", p=person(designation="emergency"), exposure=WARN)
    assert i.exposed and i.supervisor_suggestion is None and "staffing" in i.reason_text


@pytest.mark.parametrize("alert", [
    ZoneAlert("MOZ037", "Winter Storm Watch", None, None),  # watch, not warning/advisory
    ZoneAlert("MOZ037", "Winter Storm Warning", at("10:00"), at("18:00")),  # after the commute
    ZoneAlert("KSZ105", "Winter Storm Warning", None, None),  # another zone
])
def test_alert_not_covering_commute_is_not_exposure(alert):
    assert not impact("normal", exposure=Exposure(alerts=(alert,))).exposed


def test_county_code_also_matches():
    assert impact("normal", exposure=Exposure(alerts=(ZoneAlert("MOC095", "Wind Advisory", None, None),))).exposed


# ---------------------------------------------------------------- 6. accommodations (private)

def test_accommodation_early_notice_wave1():
    i = impact("delayed_arrival", p=person(accommodation_on_file=True, accommodation_needs=["early_notice"]))
    assert "ACCOMMODATION_EARLY_NOTICE" in i.codes and i.wave1


def test_mobility_dismissal_sixty_minutes_earlier():
    i = impact("early_dismissal", p=person(accommodation_on_file=True, accommodation_needs=["mobility_early_dismissal"]))
    assert "ACCOMMODATION_DISMISSAL_TIME" in i.codes and i.dismissal_time == "13:00"
    assert i.duty_instruction == "leave by 13:00" and i.notify_by == at("13:00") - timedelta(minutes=45)


@pytest.mark.parametrize("need", ["text_only", "screen_reader", "large_text", "plain_language"])
def test_format_needs_shape_rendering(need):
    i = impact("closure", p=person(accommodation_on_file=True, accommodation_needs=[need]))
    assert "ACCOMMODATION_FORMAT" in i.codes and need in i.render


def test_accommodation_details_never_leave_the_engine():
    i = impact("early_dismissal", p=person(accommodation_on_file=True,
                                           accommodation_needs=["early_notice", "mobility_early_dismissal",
                                                                "screen_reader"]))
    pub = public_codes(i.codes)
    assert "ACCOMMODATION_ON_FILE" in pub and not [c for c in pub if c.startswith("ACCOMMODATION_") and c != "ACCOMMODATION_ON_FILE"]
    for text in (i.reason_text, i.audience_text):
        for word in ("early_notice", "mobility", "screen", "reader", "early notice"):
            assert word not in text.lower()
        assert "accommodation on file" in text

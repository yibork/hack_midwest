"""Delivery plan: waves, channels and the facility decision clock. Pure functions.

notify_by itself is computed per person in impact.notify_by().
Waves: 1 = emergency, facility ops, accommodation early notice, call-ins, and anyone whose
notify_by is within 60 minutes; 2 = other action-required; "fyi" last.
"""
from datetime import datetime, timedelta

from ..schemas import DecisionClock, Employee
from .impact import Impact, short_name

URGENT = timedelta(minutes=60)
QUIET_HOURS_END = 6  # action-required messages sent before 06:00 local go by push and email


def wave_for(message_type: str, *, wave1: bool, notify_by: datetime | None, now: datetime) -> str:
    if message_type in ("call_in", "staffing_alert", "access_list"):
        return "1"
    if message_type == "action_required":
        if wave1 or (notify_by is not None and notify_by - now <= URGENT):
            return "1"
        return "2"
    return "fyi"


def channels_for(person: Employee, message_type: str, now_local: datetime) -> list[str]:
    """FYI-type messages: email only (never push). Action-required: the first preferred channel,
    or push + email before 06:00 local. If push fails at send time, email is used immediately."""
    if message_type in ("fyi", "supervisor_summary", "access_list"):
        return ["email"]
    if message_type in ("action_required", "call_in") and now_local.hour < QUIET_HOURS_END:
        return ["push", "email"]
    if message_type == "staffing_alert":
        return ["push", "email"]
    return [(person.preferred_channels or ["email"])[0]]


def decision_clock(impacts: list[Impact]) -> DecisionClock:
    """min(notify_by) over action-required recipients, naming the person who drives it."""
    due = [i for i in impacts if i.message_type == "action_required" and i.notify_by]
    if not due:
        return DecisionClock(label="no deadline: nobody needs to act")
    first = min(due, key=lambda i: (i.notify_by, i.employee.name))
    if first.dismissal_time:
        detail = f"dismissal {first.dismissal_time}"
    else:
        detail = f"{first.shift.label()}, {first.employee.commute_minutes} min commute"
    return DecisionClock(at=first.notify_by, employee_id=first.employee.id,
                         label=f"first: {short_name(first.employee.name)}, {detail}")

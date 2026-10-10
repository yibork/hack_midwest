"""Critical function staffing check. Pure functions. Never assigns anyone: the manager picks
call-ins from the ranked backups."""
from dataclasses import dataclass

from ..notify.impact import Impact
from ..schemas import BackupCandidate, CalledIn, CoverageItem, CriticalFunction

MAX_BACKUPS = 5


@dataclass(frozen=True)
class CallIn:
    notification_id: str
    employee_id: str
    function_code: str
    acknowledged: bool


def is_available(imp: Impact, posture: str) -> bool:
    """Counts toward coverage: emergency-designated and scheduled (or callable, i.e. teleworking,
    when the posture isn't normal), or anyone whose instruction keeps them in the building.
    Never people on leave, on detail elsewhere, or exposed."""
    if imp.exposed or imp.commute_risk or imp.scheduled in ("leave", "day_off", "detail_out"):
        return False
    if imp.employee.designation == "emergency":
        return imp.scheduled == "onsite" or (imp.scheduled == "telework" and posture != "normal")
    return imp.onsite_expected


def coverage(functions: list[CriticalFunction], impacts: list[Impact], posture: str,
             call_ins: list[CallIn] = (), day=None, workday: bool = True) -> list[CoverageItem]:
    """day/workday: a function with its own operating_days (e.g. animal care, 7 days) is required
    on them; others only on the facility's operating days."""
    items = []
    for fn in functions:
        if fn.operating_days is not None and day is not None:
            runs_today = day.weekday() in fn.operating_days
        else:
            runs_today = workday
        required = int(fn.min_onsite.get(posture, 0)) if runs_today else 0
        qualified = [i for i in impacts if fn.code in i.employee.functions]
        available = [i for i in qualified if is_available(i, posture)]
        avail_ids = {i.employee.id for i in available}
        mine = [c for c in call_ins if c.function_code == fn.code and c.employee_id not in avail_ids]
        names = {i.employee.id: i.employee.name for i in impacts}
        acked = [c for c in mine if c.acknowledged]
        called_ids = {c.employee_id for c in mine}
        backups = sorted(
            (i for i in qualified if i.employee.id not in avail_ids and i.employee.id not in called_ids
             and not (i.exposed or i.commute_risk) and i.scheduled not in ("leave", "detail_out")),
            key=lambda i: (i.employee.commute_minutes, i.employee.name))[:MAX_BACKUPS]
        have = len(available) + len(acked)
        if have >= required:
            status = "covered"
        elif have + len(mine) - len(acked) >= required:
            status = "pending"
        else:
            status = "shortfall"
        items.append(CoverageItem(
            code=fn.code, name=fn.name, required=required,
            available=[i.employee.name for i in available],
            exposed=[i.employee.name for i in qualified if i.exposed or i.commute_risk],
            called_in=[CalledIn(employee_id=c.employee_id, name=names.get(c.employee_id, c.employee_id),
                                notification_id=c.notification_id, acknowledged=c.acknowledged) for c in mine],
            backups=[BackupCandidate(employee_id=i.employee.id, name=i.employee.name,
                                     designation=i.employee.designation,
                                     commute_minutes=i.employee.commute_minutes, scheduled=i.scheduled)
                     for i in backups] if status != "covered" else [],
            gap=max(0, required - have), status=status,
            reason_code="STAFFING_COVERED" if status == "covered" else "STAFFING_SHORTFALL",
            label=f"{fn.name} {have}/{required} - {status}",
        ))
    return items

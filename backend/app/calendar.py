"""Operating calendar: facility operating days + the US federal holiday calendar. Pure.

Federal holidays (5 U.S.C. 6103), with the observed-day rule for Saturday/Sunday holidays:
New Year's Day, Martin Luther King Jr. Day, Washington's Birthday, Memorial Day, Juneteenth,
Independence Day, Labor Day, Columbus Day, Veterans Day, Thanksgiving Day, Christmas Day.
"""
from datetime import date, timedelta

DEFAULT_DAYS = (0, 1, 2, 3, 4)  # Mon-Fri for office staff
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _nth(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def federal_holidays(year: int) -> dict[date, str]:
    fixed = {
        "New Year's Day": date(year, 1, 1), "Juneteenth National Independence Day": date(year, 6, 19),
        "Independence Day": date(year, 7, 4), "Veterans Day": date(year, 11, 11), "Christmas Day": date(year, 12, 25),
    }
    out = {_observed(d): name for name, d in fixed.items()}
    out.update({
        _nth(year, 1, 0, 3): "Martin Luther King Jr. Day", _nth(year, 2, 0, 3): "Washington's Birthday",
        _last(year, 5, 0): "Memorial Day", _nth(year, 9, 0, 1): "Labor Day", _nth(year, 10, 0, 2): "Columbus Day",
        _nth(year, 11, 3, 4): "Thanksgiving Day",
    })
    nxt = _observed(date(year + 1, 1, 1))  # Jan 1 on a Saturday is observed on Dec 31
    if nxt.year == year:
        out[nxt] = "New Year's Day"
    return out


def holiday(d: date) -> str | None:
    return federal_holidays(d.year).get(d)


def is_operating_day(d: date, days: list[int] | tuple[int, ...] | None = None) -> bool:
    return d.weekday() in (days if days is not None else DEFAULT_DAYS) and holiday(d) is None


def why_not(d: date, days=None) -> str | None:
    h = holiday(d)
    if h:
        return f"{label(d)} is a federal holiday ({h})"
    if d.weekday() not in (days if days is not None else DEFAULT_DAYS):
        return f"{label(d)} is not an operating day"
    return None


def label(d: date) -> str:
    return f"{DAY_NAMES[d.weekday()]} {d:%b} {d.day}"


def next_operating_day(start: date, days=None, horizon: int = 14) -> tuple[date, str]:
    """-> (first operating day on or after start, explanation), e.g.
    "Sat Oct 10-Sun Oct 11 is the weekend; Mon Oct 12 is a federal holiday (Columbus Day); next workday Tue Oct 13."
    """
    skipped: list[date] = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        if is_operating_day(d, days):
            return d, (f"{_explain(skipped, days)}; next workday {label(d)}." if skipped
                       else f"Next operating day: {label(d)}.")
        skipped.append(d)
    raise ValueError("no operating day in the next two weeks")


def _explain(skipped: list[date], days) -> str:
    parts, run = [], []
    for d in skipped + [None]:
        if d is not None and holiday(d) is None:
            run.append(d)
            continue
        if run:
            weekend = all(x.weekday() >= 5 for x in run)
            span = label(run[0]) if len(run) == 1 else f"{label(run[0])}-{label(run[-1])}"
            parts.append(f"{span} is {'the weekend' if weekend else 'not an operating day'}")
            run = []
        if d is not None:
            parts.append(why_not(d, days))
    return "; ".join(parts)

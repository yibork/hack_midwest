"""Personal impact engine. Pure functions: no database, no network, no LLM.

For each person linked to the facility on the operating date it decides, in this order, and
records a reason code for every step that applied:

1. Assignment     DETAIL_OUT (excluded) / DETAIL_IN (treated as local)
2. Schedule       LEAVE / DAY_OFF (excluded; FYI only on closure) / TELEWORK_SCHEDULED (FYI email)
3. Shift overlap  SHIFT_NOT_AFFECTED when the posture's effect window misses the shift
4. Role           EMERGENCY_DUTY / FACILITY_OPS / CONTRACTOR_STATUS_ONLY / SUPERVISOR_SUMMARY /
                  SECURITY_ACCESS_LIST, else SCHEDULED_ONSITE
5. Exposure       HOME_IN_WARNING_AREA / ROUTE_CLOSED (also when the facility posture is normal).
                  Nobody is told to stay home by the system: the supervisor gets a suggestion.
6. Accommodations ACCOMMODATION_EARLY_NOTICE / ACCOMMODATION_DISMISSAL_TIME / ACCOMMODATION_FORMAT.
                  These are private: public_codes() maps them to ACCOMMODATION_ON_FILE, and the
                  reason and audience texts only ever say "accommodation on file".

Times are facility-local. A shift whose end is before its start ends the next day.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..access.policy import Access, access_for
from ..routes import normalize_routes
from ..schemas import Assignment, Employee, Shift
from . import instructions as ins

PRIVATE_CODES = {"ACCOMMODATION_EARLY_NOTICE", "ACCOMMODATION_DISMISSAL_TIME", "ACCOMMODATION_FORMAT"}
RENDER_NEEDS = ("text_only", "screen_reader", "large_text", "plain_language")
WARNING_WORDS = ("Warning", "Advisory")


def public_codes(codes: list[str]) -> list[str]:
    """Codes safe to store and show to managers and supervisors."""
    out: list[str] = []
    for c in codes:
        c = "ACCOMMODATION_ON_FILE" if c in PRIVATE_CODES else c
        if c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------- inputs

def hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def fmt(t: datetime | None) -> str | None:
    return t.strftime("%H:%M") if t else None


@dataclass(frozen=True)
class PostureContext:
    facility_id: str
    facility_name: str
    timezone: str
    operating_date: date
    posture: str
    open_time: str  # "HH:MM"
    close_time: str
    now: datetime
    delay_hours: int | None = None  # None: instructions.yaml default
    dismissal_time: str | None = None  # None: close - default hours
    critical_codes: frozenset[str] = frozenset()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def at(self, hm: str, day: date | None = None) -> datetime:
        return datetime.combine(day or self.operating_date, hhmm(hm), self.tz)

    @property
    def delay(self) -> timedelta:
        return timedelta(hours=self.delay_hours or ins.defaults()["delay_hours"])

    @property
    def open_dt(self) -> datetime:
        return self.at(self.open_time)

    @property
    def close_dt(self) -> datetime:
        return self.at(self.close_time)

    @property
    def facility_report_dt(self) -> datetime:
        return self.open_dt + self.delay

    @property
    def dismissal_dt(self) -> datetime:
        if self.dismissal_time:
            return self.at(self.dismissal_time)
        return self.close_dt - timedelta(hours=ins.defaults()["early_dismissal_hours_before_close"])

    @property
    def day_start(self) -> datetime:
        return datetime.combine(self.operating_date, time(0, 0), self.tz)

    def params(self) -> dict:
        """What a Notice stores and the UI shows."""
        p: dict = {"posture": self.posture}
        if self.posture == "delayed_arrival":
            p.update(delay_hours=int(self.delay.total_seconds() // 3600),
                     facility_report_time=fmt(self.facility_report_dt))
        if self.posture == "early_dismissal":
            p.update(dismissal_time=fmt(self.dismissal_dt))
        return p

    def effect_window(self) -> tuple[datetime, datetime] | None:
        """When the posture changes what people do. None for normal operations."""
        if self.posture == "delayed_arrival":
            return self.day_start, self.facility_report_dt  # shifts *starting* in here are delayed
        if self.posture == "early_dismissal":
            return self.dismissal_dt, self.close_dt
        if self.posture in ("telework", "closure"):
            return self.day_start, self.day_start + timedelta(days=1)
        return None


@dataclass(frozen=True)
class ZoneAlert:
    zone: str  # forecast zone or county code
    event: str
    onset: datetime | None
    ends: datetime | None


@dataclass(frozen=True)
class Exposure:
    """Active NWS alerts by home zone/county, and closed routes (MoDOT evidence, when present)."""
    alerts: tuple[ZoneAlert, ...] = ()
    closed_routes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ShiftWindow:
    start: datetime
    end: datetime
    starts_previous_day: bool = False

    def overlaps(self, a: datetime, b: datetime) -> bool:
        return self.start < b and a < self.end

    def label(self) -> str:
        s = fmt(self.start)
        if self.end.date() > self.start.date():
            return f"night shift {s}"
        if self.start.hour < 6:
            return f"early shift {s}"
        return f"shift {s}"


def shift_window(shift: Shift, tz: ZoneInfo) -> ShiftWindow | None:
    if shift.status != "onsite" or not shift.shift_start or not shift.shift_end:
        return None
    start = datetime.combine(shift.date, hhmm(shift.shift_start), tz)
    end = datetime.combine(shift.date, hhmm(shift.shift_end), tz)
    if end <= start:
        end += timedelta(days=1)
    return ShiftWindow(start, end)


# ---------------------------------------------------------------- output

@dataclass
class Impact:
    employee: Employee
    codes: list[str] = field(default_factory=list)  # internal; includes private accommodation codes
    reasons: list[str] = field(default_factory=list)  # manager-readable, privacy-safe
    audience: list[str] = field(default_factory=list)  # supervisor-readable
    message_type: str = "none"  # action_required | fyi | none
    duty_instruction: str = ""
    instruction_key: str = ""  # which table entry, for grouping messages
    access: Access | None = None
    shift: ShiftWindow | None = None
    scheduled: str = "day_off"
    report_time: str | None = None
    dismissal_time: str | None = None
    notify_by: datetime | None = None
    requires_ack: bool = False
    wave1: bool = False  # emergency, facility_ops, early notice (delivery adds "due within 60 min")
    onsite_expected: bool = False  # at the building during the operating day (staffing check)
    exposed: bool = False  # exposed on today's commute (acted on: FYI, supervisor suggestion)
    commute_risk: bool = False  # would be exposed if they came in (staffing: backups, call-ins)
    exposure_detail: list[str] = field(default_factory=list)  # manager-readable
    supervisor_suggestion: str | None = None
    render: frozenset[str] = frozenset()  # private formatting needs
    linked: bool = True
    detail_facility: str | None = None

    @property
    def public_codes(self) -> list[str]:
        return public_codes(self.codes)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons)

    @property
    def audience_text(self) -> str:
        return "; ".join(self.audience)

    def add(self, code: str, reason: str, audience: str | None = None) -> None:
        if code not in self.codes:
            self.codes.append(code)
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        if audience and audience not in self.audience:
            self.audience.append(audience)


def short_name(name: str) -> str:
    parts = name.split()
    return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else name


# ---------------------------------------------------------------- the engine

def _active(a: Assignment, day: date) -> bool:
    return (a.start_date is None or a.start_date <= day) and (a.end_date is None or a.end_date >= day)


def linked_people(facility_id: str, day: date, people: list[Employee],
                  assignments: list[Assignment]) -> list[tuple[Employee, list[Assignment]]]:
    """People linked to the facility on the day: permanent here, or detailed in here."""
    by_person: dict[str, list[Assignment]] = {}
    for a in assignments:
        if _active(a, day):
            by_person.setdefault(a.employee_id, []).append(a)
    out = []
    for p in people:
        mine = by_person.get(p.id, [])
        if any(a.facility_id == facility_id and a.kind in ("permanent", "detail_in") for a in mine):
            out.append((p, mine))
    return out


def _alert_hits(person: Employee, exposure: Exposure, start: datetime, end: datetime) -> list[ZoneAlert]:
    zones = {z for z in (person.home_zone, person.home_county) if z}
    hits = []
    for a in exposure.alerts:
        if a.zone not in zones or not any(a.event.endswith(w) for w in WARNING_WORDS):
            continue
        if (a.onset is None or a.onset < end) and (a.ends is None or a.ends > start):
            hits.append(a)
    return hits


def _commute_risk(person: Employee, exposure: Exposure, start: datetime, end: datetime) -> bool:
    return bool(_alert_hits(person, exposure, start, end) or normalize_routes(person.commute_routes) & normalize_routes(exposure.closed_routes))


def compute_impact(ctx: PostureContext, person: Employee, assignments: list[Assignment],
                   shifts: list[Shift], exposure: Exposure,
                   facility_names: dict[str, str] | None = None) -> Impact:
    """One person's impact. `shifts` may include the previous day's (for overnight shifts)."""
    imp = Impact(employee=person)
    lang = person.language
    day, posture = ctx.operating_date, ctx.posture
    label = ins.posture_label(posture)
    d = ins.defaults()

    # ---- 1. assignment
    out = next((a for a in assignments if a.kind == "detail_out" and _active(a, day)), None)
    if out:
        name = (facility_names or {}).get(out.facility_id, out.facility_id)
        until = out.end_date.isoformat() if out.end_date else "further notice"
        imp.detail_facility = name
        imp.add("DETAIL_OUT", ins.text("detail_out", "en", facility_name=name, date=until),
                f"on detail to {name} until {until}")
        imp.duty_instruction = ins.text("detail_out", lang, facility_name=name, date=until)
        imp.scheduled = "detail_out"
        return imp
    if any(a.kind == "detail_in" and a.facility_id == ctx.facility_id and _active(a, day) for a in assignments):
        until = next(a.end_date for a in assignments if a.kind == "detail_in")
        imp.add("DETAIL_IN", f"detailed in to this facility until {until or 'further notice'}; treated as local",
                "on detail here; treated as local")

    # ---- 2. schedule
    today = next((s for s in shifts if s.date == day), None)
    yesterday = next((s for s in shifts if s.date == day - timedelta(days=1)), None)
    status = today.status if today else "day_off"
    imp.scheduled = status
    windows: list[ShiftWindow] = []
    if yesterday:
        w = shift_window(yesterday, ctx.tz)
        if w and w.end > ctx.day_start:  # overnight shift running into the operating day
            windows.append(ShiftWindow(w.start, w.end, starts_previous_day=True))
    if today:
        w = shift_window(today, ctx.tz)
        if w:
            windows.append(w)

    if status in ("telework", "day_off"):
        # Not commuting today, but they could be called in: arrival at the facility's opening.
        arrive = ctx.facility_report_dt if posture == "delayed_arrival" else ctx.open_dt
        imp.commute_risk = _commute_risk(person, exposure, arrive - timedelta(minutes=person.commute_minutes), arrive)
    if status in ("leave", "day_off") and not windows:
        code = "LEAVE" if status == "leave" else "DAY_OFF"
        imp.add(code, "on leave today" if status == "leave" else "not scheduled today (day off)",
                "on leave" if status == "leave" else "day off")
        imp.duty_instruction = ins.text("not_scheduled", lang)
        if posture == "closure":
            imp.message_type = "fyi"
        _access(imp, ctx)
        return imp
    if status == "telework":
        imp.add("TELEWORK_SCHEDULED", "scheduled to telework today; no change", "teleworking; no change")
        imp.duty_instruction = ins.text("telework_scheduled", lang)
        imp.instruction_key = "telework_scheduled"
        if posture != "normal":
            imp.message_type = "fyi"  # email only, never push (delivery enforces)
        _access(imp, ctx)
        return imp

    # ---- 3. shift overlap
    window = ctx.effect_window()
    shift = next(iter(windows), None)
    if window:
        a, b = window
        if posture == "delayed_arrival":
            hit = [w for w in windows if a <= w.start < b]  # arrivals inside the delay window
        else:
            hit = [w for w in windows if w.overlaps(a, b)]
        shift = hit[0] if hit else next((w for w in windows if not w.starts_previous_day), shift)
        affected = bool(hit)
    else:
        affected = False
    imp.shift = shift
    emergency = person.designation == "emergency"
    if window and not affected and not emergency:
        imp.add("SHIFT_NOT_AFFECTED", f"{shift.label() if shift else 'shift'} is outside the {label} window",
                f"shift outside the {label} window")
        imp.duty_instruction = ins.text("shift_not_affected", lang, posture_label=ins.posture_label(posture, lang))

    # Personal times (step 6's mobility accommodation feeds the dismissal time).
    if shift and posture == "delayed_arrival" and not emergency:
        imp.report_time = fmt(shift.start + ctx.delay)
    dismissal = ctx.dismissal_dt
    mobility = "mobility_early_dismissal" in person.accommodation_needs
    if posture == "early_dismissal" and mobility and affected and not emergency:
        dismissal -= timedelta(minutes=d["mobility_dismissal_minutes_earlier"])
    if posture == "early_dismissal":
        imp.dismissal_time = fmt(dismissal)

    # ---- 4. designation / role
    values = dict(report_time=imp.report_time or fmt(ctx.facility_report_dt), dismissal_time=imp.dismissal_time,
                  delay_hours=int(ctx.delay.total_seconds() // 3600), facility_name=ctx.facility_name,
                  facility_report_time=fmt(ctx.facility_report_dt))
    onsite = True
    if posture == "normal" or (window and not affected and not emergency):
        imp.duty_instruction = imp.duty_instruction or ins.duty("normal", person.designation, lang)[0]
        imp.instruction_key = "normal" if posture == "normal" else "shift_not_affected"
    elif person.role == "contractor":
        acc = _access(imp, ctx)
        imp.add("CONTRACTOR_STATUS_ONLY", "contractor: building status only, no duty instructions",
                "contractor: building status only")
        imp.duty_instruction = ins.contractor(acc.status, lang, acc.time)
        imp.instruction_key = f"contractor:{acc.status}"
        imp.message_type = "action_required"
        onsite = acc.status != "denied"
    else:
        imp.duty_instruction, onsite = ins.duty(posture, person.designation, lang, **values)
        imp.instruction_key = f"{posture}:{person.designation}"
        imp.message_type = "action_required"
        if emergency:
            imp.add("EMERGENCY_DUTY", f"emergency-designated: {ins.duty(posture, 'emergency', 'en')[0]}",
                    "emergency-designated")
            imp.requires_ack = imp.wave1 = True
        if person.role == "facility_ops":
            action = ins.facility_ops_action(posture, lang, **values)
            if action:
                imp.duty_instruction = f"{imp.duty_instruction}; {action}"
            imp.add("FACILITY_OPS", "facility operations: building actions for this posture",
                    "facility operations")
            imp.requires_ack = imp.wave1 = True
            imp.instruction_key += ":facility_ops"
        if not emergency and person.role != "facility_ops":
            imp.add("SCHEDULED_ONSITE", f"scheduled onsite ({shift.label() if shift else 'shift'}); "
                                        f"{person.designation.replace('_', ' ')}",
                    f"scheduled onsite; {person.designation.replace('_', ' ')}")
    if person.role in ("supervisor", "facility_manager"):
        imp.add("SUPERVISOR_SUMMARY", "receives the team summary", "receives the team summary")
    if person.role == "security_desk":
        imp.add("SECURITY_ACCESS_LIST", "receives the building access list", "receives the access list")
    imp.onsite_expected = bool(shift) and onsite

    # ---- 5. personal exposure (commute into the building). "report, or request leave" still commutes.
    commuting = imp.onsite_expected or (bool(shift) and posture == "telework" and person.designation == "non_telework"
                                        and person.role != "contractor")
    if shift:
        if shift.starts_previous_day:  # commuting home at the end of the night shift
            c_start, c_end = shift.end, shift.end + timedelta(minutes=person.commute_minutes)
        else:
            arrive = ctx.at(imp.report_time) if imp.report_time else shift.start
            c_start, c_end = arrive - timedelta(minutes=person.commute_minutes), arrive
        imp.commute_risk = _commute_risk(person, exposure, c_start, c_end)
    if shift and commuting:
        hits = _alert_hits(person, exposure, c_start, c_end)
        routes = sorted(normalize_routes(person.commute_routes) & normalize_routes(exposure.closed_routes))
        if hits:
            imp.exposure_detail.append(", ".join(sorted({f"{h.event} ({h.zone})" for h in hits})))
            imp.add("HOME_IN_WARNING_AREA", f"home area under {imp.exposure_detail[-1]} during the commute "
                                            f"{fmt(c_start)}-{fmt(c_end)}",
                    "commute may be affected by weather")
        if routes:
            imp.exposure_detail.append(f"route closed: {', '.join(routes)}")
            imp.add("ROUTE_CLOSED", f"commute route closed: {', '.join(routes)}",
                    "a commute route is closed")
        if hits or routes:
            imp.exposed = True
            if emergency:
                imp.reasons.append("exposed emergency employee: flagged in the staffing check")
            else:
                imp.supervisor_suggestion = ins.text("exposure_suggestion", "en")
                if imp.message_type == "none":
                    imp.message_type = "fyi"
                    imp.duty_instruction = ins.text("exposure_fyi", lang)
                    imp.instruction_key = "exposure_fyi"

    # ---- 6. accommodations (private; never named outside the person's own message)
    if person.accommodation_needs and imp.message_type != "none":
        if "early_notice" in person.accommodation_needs:
            imp.add("ACCOMMODATION_EARLY_NOTICE", "accommodation on file: notified in the first wave",
                    "accommodation on file")
            imp.wave1 = True
        if mobility and posture == "early_dismissal" and affected and not emergency:
            imp.add("ACCOMMODATION_DISMISSAL_TIME", f"accommodation on file: personal dismissal time {imp.dismissal_time}",
                    "accommodation on file")
        render = frozenset(n for n in person.accommodation_needs if n in RENDER_NEEDS)
        if render:
            imp.render = render
            imp.add("ACCOMMODATION_FORMAT", "accommodation on file: message format adjusted", "accommodation on file")

    # ---- building access and notify-by
    if imp.access is None:
        _access(imp, ctx)
    imp.notify_by = notify_by(ctx, imp)
    return imp


def _access(imp: Impact, ctx: PostureContext) -> Access:
    p = imp.employee
    imp.access = access_for(p.access_level, ctx.posture,
                            report_time=imp.report_time or fmt(ctx.facility_report_dt),
                            dismissal_time=imp.dismissal_time or fmt(ctx.dismissal_dt),
                            functions=p.functions, critical_codes=ctx.critical_codes)
    return imp.access


def notify_by(ctx: PostureContext, imp: Impact) -> datetime | None:
    """Latest time the person should hear about it.
    Early dismissal: personal dismissal time - 45 min. Otherwise shift start - commute - 30 min
    (an overnight shift that started yesterday: its end - 45 min, before they head home)."""
    if imp.message_type != "action_required" or imp.shift is None:
        return None
    d = ins.defaults()
    if ctx.posture == "early_dismissal":
        return ctx.at(imp.dismissal_time) - timedelta(minutes=d["dismissal_notify_lead_minutes"])
    if imp.shift.starts_previous_day:
        return imp.shift.end - timedelta(minutes=d["dismissal_notify_lead_minutes"])
    return imp.shift.start - timedelta(minutes=imp.employee.commute_minutes + d["notify_lead_minutes"])


def compute_impacts(ctx: PostureContext, people: list[Employee], assignments: list[Assignment],
                    shifts: list[Shift], exposure: Exposure,
                    facility_names: dict[str, str] | None = None) -> list[Impact]:
    """Everyone linked to the facility on the operating date, plus people detailed out (excluded)."""
    by_person_shifts: dict[str, list[Shift]] = {}
    for s in shifts:
        by_person_shifts.setdefault(s.employee_id, []).append(s)
    out = []
    for person, mine in linked_people(ctx.facility_id, ctx.operating_date, people, assignments):
        out.append(compute_impact(ctx, person, mine, by_person_shifts.get(person.id, []), exposure, facility_names))
    return out

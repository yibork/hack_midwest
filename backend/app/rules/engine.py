"""Deterministic rules engine. Plain Python, no LLM, no network.

evaluate(facility, evidence_list, now) -> Recommendation is a pure function: the same inputs
always give the same posture, triggers, confidence and rationale.
"""
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..schemas import POSTURE_ORDER, Evidence, Facility, Recommendation, Trigger
from . import local as local_rules

POLICY_PATH = Path(__file__).with_name("thresholds.yaml")
REQUIRED_KINDS = {"active_alerts": "active NWS alerts", "wind_forecast": "wind/gust forecast"}
CONFIDENT_CERTAINTY = {"Observed", "Likely"}
NEAR_THRESHOLD_MPH = 5.0


def rank(posture: str) -> int:
    return POSTURE_ORDER.index(posture)


# ---------------------------------------------------------------- policy

@dataclass(frozen=True)
class Policy:
    version: str
    rules: list[dict]
    commute_window_hours: float = 2
    departure_window_hours: float = 2
    local: dict | None = None  # local_signals section (rules/local.py)


def load_policy(path: Path = POLICY_PATH) -> Policy:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    # Version + content hash, so a replayed decision points at the exact rule text used.
    version = f"{data['version']}+{hashlib.sha256(raw).hexdigest()[:8]}"
    return Policy(version=version, rules=data["rules"],
                  commute_window_hours=data.get("commute_window_hours", 2),
                  departure_window_hours=data.get("departure_window_hours", 2),
                  local=data.get("local_signals") or {})


# ---------------------------------------------------------------- time windows

@dataclass(frozen=True)
class Windows:
    operating_date: date
    open: datetime
    close: datetime
    commute_start: datetime
    commute_end: datetime
    departure_start: datetime
    departure_end: datetime

    def get(self, name: str) -> tuple[datetime, datetime]:
        return {
            "commute": (self.commute_start, self.commute_end),
            "operating_hours": (self.open, self.close),
            "departure": (self.departure_start, self.departure_end),
            "operating_day": (self.commute_start, self.departure_end),
        }[name]


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def compute_windows(facility: Facility, now: datetime, policy: Policy, operating_date: date | None = None) -> Windows:
    """The operating date is the given one; by default today until today's close time, then
    tomorrow (the pipeline passes the next operating day, see app/calendar.py)."""
    tz = ZoneInfo(facility.timezone)
    local_now = now.astimezone(tz)
    day = local_now.date()
    if operating_date is not None:
        day = operating_date
    elif local_now >= datetime.combine(day, _hhmm(facility.close_time), tz):
        day += timedelta(days=1)
    open_dt = datetime.combine(day, _hhmm(facility.open_time), tz)
    close_dt = datetime.combine(day, _hhmm(facility.close_time), tz)
    return Windows(
        operating_date=day, open=open_dt, close=close_dt,
        commute_start=open_dt - timedelta(hours=policy.commute_window_hours), commute_end=open_dt,
        departure_start=close_dt, departure_end=close_dt + timedelta(hours=policy.departure_window_hours),
    )


def _fmt(t: datetime) -> str:
    return t.strftime("%H:%M")


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- evidence helpers

def effective_status(ev: Evidence, now: datetime) -> str:
    if ev.status == "ok" and ev.valid_until and ev.valid_until < now:
        return "stale"
    return ev.status


def _latest_by_kind(evidence: list[Evidence]) -> dict[str, Evidence]:
    out: dict[str, Evidence] = {}
    for ev in sorted(evidence, key=lambda e: e.retrieved_at):
        out[ev.kind] = ev
    return out


def peak_gust(wind: Evidence | None, start: datetime, end: datetime) -> float | None:
    """Highest forecast gust for hours starting inside [start, end). None if no data."""
    if wind is None or wind.status == "unknown" or not isinstance(wind.value, dict):
        return None
    gusts = [h["gust_mph"] for h in wind.value.get("hours", [])
             if h.get("gust_mph") is not None and start <= _parse_dt(h["time"]) < end]
    return max(gusts) if gusts else None


def _alert_overlaps(alert: dict, start: datetime, end: datetime, now: datetime) -> bool:
    onset = _parse_dt(alert.get("onset")) or now
    ends = _parse_dt(alert.get("ends"))  # None = open-ended
    return onset < end and (ends is None or ends > start)


# ---------------------------------------------------------------- clause checks

def _check_alert(rule: dict, clause: dict, alerts: list[dict], alerts_ev: Evidence | None,
                 w: Windows, now: datetime) -> tuple[Trigger, dict] | None:
    start, end = w.get(clause.get("window", "operating_day"))
    for a in alerts:
        if "events" in clause and a.get("event") not in clause["events"]:
            continue
        if "severity" in clause and a.get("severity") not in clause["severity"]:
            continue
        if "urgency" in clause and a.get("urgency") not in clause["urgency"]:
            continue
        if not _alert_overlaps(a, start, end, now):
            continue
        posture = rule["posture"]
        onset = _parse_dt(a.get("onset")) or now
        if rule.get("posture_if_onset_before_open") and onset <= w.open:
            posture = rule["posture_if_onset_before_open"]
        criteria = []
        if "events" in clause:
            criteria.append(" or ".join(clause["events"]))
        if "severity" in clause:
            criteria.append("severity " + "/".join(clause["severity"]))
        if "urgency" in clause:
            criteria.append("urgency " + "/".join(clause["urgency"]))
        ends = _parse_dt(a.get("ends"))
        span = f"{_fmt(onset.astimezone(w.open.tzinfo))}–{_fmt(ends.astimezone(w.open.tzinfo)) if ends else 'until further notice'}"
        return Trigger(
            rule_id=rule["id"], description=rule["description"],
            threshold=", ".join(criteria) + f" during {clause.get('window', 'operating_day').replace('_', ' ')}",
            observed=f"{a.get('event')} ({a.get('severity')}/{a.get('urgency')}/{a.get('certainty')}), {span}",
            citation=rule["citation"], posture=posture,
            evidence_id=alerts_ev.id if alerts_ev else None,
        ), a
    return None


def _check_gust(rule: dict, clause: dict, peak: float | None, wind_ev: Evidence | None) -> Trigger | None:
    if peak is None:
        return None
    lo, hi = clause["min_mph"], clause.get("below_mph")
    if peak < lo or (hi is not None and peak >= hi):
        return None
    return Trigger(
        rule_id=rule["id"], description=rule["description"],
        threshold=lo, observed=peak, margin=round(peak - lo, 1), units="mph",
        citation=rule["citation"], posture=rule["posture"],
        evidence_id=wind_ev.id if wind_ev else None,
    )


# ---------------------------------------------------------------- evaluate

def evaluate(facility: Facility, evidence_list: list[Evidence], now: datetime,
             policy: Policy | None = None, operating_date: date | None = None,
             target_note: str | None = None, target_mode: str = "next_operating_day") -> Recommendation:
    policy = policy or load_policy()
    w = compute_windows(facility, now, policy, operating_date)
    by_kind = _latest_by_kind(evidence_list)

    alerts_ev = by_kind.get("active_alerts")
    alerts = alerts_ev.value.get("alerts", []) if alerts_ev and alerts_ev.status != "unknown" else []
    wind_ev = by_kind.get("wind_forecast")
    commute_passed = now >= w.commute_end
    commute_peak = peak_gust(wind_ev, w.commute_start, w.commute_end)
    departure_peak = peak_gust(wind_ev, w.departure_start, w.departure_end)

    # 1. Fire rules. One trigger per rule (first matching clause).
    triggers: list[Trigger] = []
    fired_alerts: list[dict] = []
    for rule in policy.rules:
        for clause in rule["condition"]["any"]:
            if clause["type"] == "alert":
                hit = _check_alert(rule, clause, alerts, alerts_ev, w, now)
                t = hit[0] if hit else None
                if hit:
                    fired_alerts.append(hit[1])
            elif clause["type"] == "forecast_gust":
                t = _check_gust(rule, clause, commute_peak, wind_ev)
            else:
                raise ValueError(f"unknown clause type {clause['type']!r} in rule {rule['id']}")
            if t:
                triggers.append(t)
                break

    # 1b. Local signals (validated facts from allowlisted sources). Can only add triggers and
    #     notes; the max() below means they can never lower the posture.
    weather_fired = any(t.posture != "normal" for t in triggers)
    area = by_kind.get("people_area")
    area_v = area.value if area and isinstance(area.value, dict) else {}
    local = local_rules.apply(facility, evidence_list, (w.commute_start, w.commute_end), weather_fired,
                              policy.local or {}, area_v.get("facility_county"), area_v.get("home_counties"))
    triggers += local.triggers

    # 2. Most restrictive posture wins.
    posture = max((t.posture for t in triggers), key=rank, default="normal")

    # 3. Confidence. Missing data is never treated as safe: it lowers confidence and says so.
    level, reasons = 0, []

    def lower(to: int, reason: str):
        nonlocal level
        level = max(level, to)
        reasons.append(reason)

    for kind, label in REQUIRED_KINDS.items():
        if kind not in by_kind:
            lower(2, f"No {label} evidence was collected; conditions cannot be confirmed safe.")
    for ev in evidence_list:
        if ev.source == "local":  # handled by rules/local.py (missing local data is noted, not "safe")
            continue
        st = effective_status(ev, now)
        if st == "unknown":
            err = ev.value.get("error") if isinstance(ev.value, dict) else None
            lower(2, f"{ev.source.upper()} {ev.kind.replace('_', ' ')} is unavailable"
                     f"{f' ({err})' if err else ''}; missing data is not treated as safe.")
        elif st == "stale":
            lower(1, f"{ev.source.upper()} {ev.kind.replace('_', ' ')} is stale "
                     f"(retrieved {ev.retrieved_at:%Y-%m-%d %H:%M} UTC).")
        # Flags come from the weather agent's reading of the evidence. They can lower
        # confidence; they never change the posture.
        if ev.ambiguity_flags:
            lower(1, f"Weather agent flagged {ev.kind.replace('_', ' ')}: {'; '.join(ev.ambiguity_flags)}.")
    for a in fired_alerts:
        if a.get("certainty") not in CONFIDENT_CERTAINTY:
            lower(1, f"Triggering {a.get('event')} has certainty '{a.get('certainty')}' (not Observed/Likely).")
    if wind_ev and wind_ev.status != "unknown" and not commute_passed and commute_peak is None:
        lower(1, "The wind forecast has no gust values for the commute window.")
    gust_thresholds = sorted({c["min_mph"] for r in policy.rules for c in r["condition"]["any"]
                              if c["type"] == "forecast_gust"})
    if commute_peak is not None:
        for th in gust_thresholds:
            if abs(commute_peak - th) < NEAR_THRESHOLD_MPH:
                lower(1, f"Forecast commute peak gust ({commute_peak:g} mph) is within "
                         f"{NEAR_THRESHOLD_MPH:g} mph of the {th:g} mph threshold.")
    for msg in local.lowers:
        lower(1, msg)
    if not reasons:
        reasons.append("All required sources returned current data and no values are near a threshold.")
    if local.corroborations and level > 0:  # one level at most, and only with a fired weather rule
        level -= 1
        reasons.append(local.corroborations[0] + " Confidence raised one level.")
    elif local.corroborations:
        reasons.append(local.corroborations[0])
    reasons += local.notes
    confidence = ["high", "medium", "low"][level]

    # 4. What would change the recommendation.
    changes: list[str] = []
    window_label = f"the commute window ({_fmt(w.commute_start)}–{_fmt(w.commute_end)})"
    fired_ids = {t.rule_id for t in triggers}
    gust_changes, alert_changes = [], []
    for rule in policy.rules:
        if rule["id"] in fired_ids or rank(rule["posture"]) <= rank(posture):
            continue
        for c in rule["condition"]["any"]:
            if c["type"] == "forecast_gust":
                if commute_passed:
                    continue
                peak_txt = (f"forecast peak is {commute_peak:g} mph" if commute_peak is not None
                            else "no gust forecast is available for that window")
                gap = (c["min_mph"] - commute_peak) if commute_peak is not None else 999
                gust_changes.append((gap, f"Gusts reaching {c['min_mph']:g} mph during {window_label} "
                                          f"would move this to {rule['posture']}; {peak_txt}."))
            elif c["type"] == "alert":
                what = " or ".join(c["events"]) if "events" in c else (
                    "Any " + "/".join(c.get("severity", [])) + " + " + "/".join(c.get("urgency", [])) + " NWS alert")
                alert_changes.append(f"{what} for this location "
                                     f"({c.get('window', 'operating_day').replace('_', ' ')}) "
                                     f"would move this to {rule['posture']}.")
    changes += [text for _, text in sorted(gust_changes)] + alert_changes + local.changes
    for t in triggers:
        if t.units is None and t.rule_id != "access_route_closed":
            changes.append(f"This holds while the triggering alert remains in effect: {t.observed}.")
    if alerts_ev is None or alerts_ev.status == "unknown":
        changes.insert(0, "Alert data could not be retrieved; re-evaluate once NWS alerts are reachable.")

    rec = Recommendation(
        id=uuid.uuid4().hex, facility_id=facility.id, evaluated_at=now, operating_date=w.operating_date,
        posture=posture, triggers=triggers, confidence=confidence, confidence_reasons=reasons,
        what_would_change=changes, rationale="", evidence_ids=[e.id for e in evidence_list],
        policy_version=policy.version, target_mode=target_mode, target_note=target_note,
    )
    rec.rationale = template_rationale(rec, facility, w, len(alerts), commute_peak, departure_peak,
                                       commute_passed)
    return rec


def template_rationale(rec: Recommendation, facility: Facility, w: Windows, alert_count: int,
                       commute_peak: float | None, departure_peak: float | None,
                       commute_passed: bool) -> str:
    """Deterministic rationale paragraph.

    HOOK: agents/writer.py:write_rationale() is where an LLM writer agent will replace this
    text. The writer may only rephrase; posture, triggers and confidence come from evaluate().
    """
    posture = rec.posture.replace("_", " ").upper()
    parts = [f"For {facility.name} on {w.operating_date:%A, %B %d}, the rules engine "
             f"(policy {rec.policy_version}) recommends {posture}."]
    if rec.target_note:
        parts.append(f"Target day: {rec.target_note}")
    if rec.triggers:
        for t in rec.triggers:
            if t.units:
                parts.append(f"Rule '{t.rule_id}' fired: forecast peak gust of {t.observed} {t.units} in the "
                             f"commute window meets the {t.threshold} {t.units} threshold "
                             f"(margin {t.margin:+g} {t.units}).")
            else:
                parts.append(f"Rule '{t.rule_id}' fired: {t.observed}.")
    else:
        parts.append("No policy thresholds were met.")
    parts.append(f"{alert_count} active NWS alert{'s' if alert_count != 1 else ''} cover{'s' if alert_count == 1 else ''} the location.")
    if commute_passed:
        parts.append("Today's commute window has already passed.")
    elif commute_peak is not None:
        parts.append(f"Peak forecast gust during the commute window ({_fmt(w.commute_start)}–"
                     f"{_fmt(w.commute_end)}) is {commute_peak:g} mph.")
    if departure_peak is not None:
        parts.append(f"Peak forecast gust during the departure window ({_fmt(w.departure_start)}–"
                     f"{_fmt(w.departure_end)}) is {departure_peak:g} mph.")
    parts.append(f"Confidence is {rec.confidence}: {' '.join(rec.confidence_reasons)}")
    parts.append("This is a recommendation only; a manager must approve or override it before anyone is notified.")
    return " ".join(parts)

"""Who may enter the building, by posture and access level (policy.yaml). Pure functions."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).with_name("policy.yaml")


@lru_cache
def table() -> dict:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def citation() -> str:
    return table()["citation"]


@dataclass(frozen=True)
class Access:
    status: str  # allowed | denied | allowed_with_escort | allowed_after | allowed_until
    time: str | None  # "HH:MM" for allowed_after / allowed_until
    code: str  # machine-readable reason
    reason: str  # human-readable reason

    @property
    def label(self) -> str:
        """What the security desk and the Notification store: "allowed_after 09:30"."""
        return f"{self.status} {self.time}" if self.time else self.status


def access_for(access_level: str, posture: str, *, report_time: str | None = None,
               dismissal_time: str | None = None, functions: list[str] | tuple[str, ...] = (),
               critical_codes: set[str] | frozenset[str] = frozenset()) -> Access:
    rule = table()["postures"][posture].get(access_level, "denied")
    verb, _, var = rule.partition(" ")
    label = posture.replace("_", " ")
    if verb == "denied_unless_critical":
        critical = sorted(set(functions) & set(critical_codes))
        if critical:
            return Access("allowed", None, "ACCESS_CONTRACTOR_CRITICAL",
                          f"{label}: contractor allowed for critical function {', '.join(critical)}")
        return Access("denied", None, "ACCESS_CONTRACTOR_DENIED", f"{label}: contractors denied")
    if verb == "allowed_after":
        return Access(verb, report_time, "ACCESS_AFTER_REPORT_TIME",
                      f"{label}: {access_level} access from the report time {report_time}")
    if verb == "allowed_until":
        return Access(verb, dismissal_time, "ACCESS_UNTIL_DISMISSAL",
                      f"{label}: {access_level} access until dismissal at {dismissal_time}")
    if verb == "allowed_with_escort":
        return Access(verb, None, "ACCESS_ESCORT_REQUIRED", f"{label}: escort required")
    if verb == "denied":
        return Access("denied", None, "ACCESS_DENIED_CLOSURE" if posture == "closure" else "ACCESS_DENIED",
                      f"{label}: {access_level} access denied")
    if access_level in ("emergency", "facility_ops") and posture != "normal":
        return Access("allowed", None, f"ACCESS_{access_level.upper()}",
                      f"{label}: {access_level.replace('_', ' ')} staff allowed at normal times")
    return Access("allowed", None, "ACCESS_OPEN", f"{label}: building open")

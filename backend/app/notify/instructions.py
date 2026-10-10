"""Duty instructions and fixed phrases, table-driven from instructions.yaml."""
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).with_name("instructions.yaml")


@lru_cache
def table() -> dict:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def citation() -> str:
    return table()["citation"]


def defaults() -> dict:
    return table()["defaults"]


def _lang(lang: str) -> str:
    return lang if lang in ("en", "es") else "en"


def posture_label(posture: str, lang: str = "en") -> str:
    return table()["posture_label"][posture][_lang(lang)]


def duty(posture: str, designation: str, lang: str, **values) -> tuple[str, bool]:
    """-> (instruction, onsite). onsite: whether the instruction has the person at the building."""
    row = table()["duty"][posture]
    entry = row.get(designation) or row["any"]
    return entry[_lang(lang)].format(**values), bool(entry.get("onsite"))


def facility_ops_action(posture: str, lang: str, **values) -> str | None:
    entry = table()["facility_ops_actions"].get(posture)
    return entry[_lang(lang)].format(**values) if entry else None


def contractor(status: str, lang: str, time: str | None = None) -> str:
    return table()["contractor"][status][_lang(lang)].format(time=time or "")


def text(key: str, lang: str = "en", **values) -> str:
    return table()["text"][key][_lang(lang)].format(**values)

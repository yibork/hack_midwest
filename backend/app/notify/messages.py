"""Message wording: deterministic templates (en/es), accommodation-aware rendering, and the
validator every drafted message must pass.

Every action-required message says: what happened; whether it applies to you and why
(privacy-safe); what to do and by when; building access; and the ack link if one is required.
No emoji anywhere. Accommodation needs shape the wording and format but are never named.
"""
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date

from ..access.policy import Access
from . import instructions as ins

_MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
              "octubre", "noviembre", "diciembre"]
_DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fmt_date(d: date, lang: str) -> str:
    if lang == "es":
        return f"{_DAYS_ES[d.weekday()]} {d.day} de {_MONTHS_ES[d.month - 1]}"
    return f"{d:%A}, {d:%B} {d.day}"


WHAT_HAPPENED = {
    "en": {
        "normal": "{facility} is operating normally on {date}.",
        "delayed_arrival": "{facility} has a {delay_hours}-hour delayed arrival on {date} because of weather.",
        "early_dismissal": "{facility} is dismissing early on {date} because of weather.",
        "telework": "{facility} is telework-only on {date} because of weather.",
        "closure": "{facility} is closed on {date} because of weather.",
    },
    "es": {
        "normal": "{facility} opera con normalidad el {date}.",
        "delayed_arrival": "{facility} tiene una llegada retrasada de {delay_hours} horas el {date} por el clima.",
        "early_dismissal": "{facility} tendrá salida anticipada el {date} por el clima.",
        "telework": "{facility} estará en teletrabajo solamente el {date} por el clima.",
        "closure": "{facility} está cerrada el {date} por el clima.",
    },
}
# plain_language: short sentences, everyday words.
WHAT_HAPPENED_PLAIN = {
    "en": {
        "normal": "The weather is OK. {facility} is open as usual on {date}.",
        "delayed_arrival": "Bad weather. {facility} opens late on {date}.",
        "early_dismissal": "Bad weather. {facility} closes early on {date}.",
        "telework": "Bad weather. Work from home on {date} if you can.",
        "closure": "Bad weather. {facility} is closed on {date}.",
    },
    "es": {
        "normal": "El clima está bien. {facility} abre como siempre el {date}.",
        "delayed_arrival": "Mal clima. {facility} abre tarde el {date}.",
        "early_dismissal": "Mal clima. {facility} cierra temprano el {date}.",
        "telework": "Mal clima. Trabaje desde casa el {date} si puede.",
        "closure": "Mal clima. {facility} está cerrada el {date}.",
    },
}
WHY = {
    "en": {
        "EMERGENCY_DUTY": "you are emergency-designated",
        "FACILITY_OPS": "you are on the facility operations team",
        "SCHEDULED_ONSITE": "you are scheduled at the building",
        "DETAIL_IN": "you are on detail at this office",
        "TELEWORK_SCHEDULED": "you are teleworking today",
        "LEAVE": "you are not scheduled today",
        "DAY_OFF": "you are not scheduled today",
        "CONTRACTOR_STATUS_ONLY": "you work at this building as a contractor",
        "HOME_IN_WARNING_AREA": "weather alerts cover the area near your home during your commute",
        "ROUTE_CLOSED": "a road on your usual route is closed",
        "CALL_IN": "your manager is asking you to cover a critical function",
        "SHIFT_NOT_AFFECTED": "your shift is outside the affected hours",
    },
    "es": {
        "EMERGENCY_DUTY": "usted tiene designación de emergencia",
        "FACILITY_OPS": "usted es del equipo de operaciones del edificio",
        "SCHEDULED_ONSITE": "usted tiene turno en el edificio",
        "DETAIL_IN": "usted está asignado temporalmente a esta oficina",
        "TELEWORK_SCHEDULED": "usted trabaja a distancia hoy",
        "LEAVE": "usted no tiene turno hoy",
        "DAY_OFF": "usted no tiene turno hoy",
        "CONTRACTOR_STATUS_ONLY": "usted trabaja en este edificio como contratista",
        "HOME_IN_WARNING_AREA": "hay alertas del clima cerca de su casa durante su trayecto",
        "ROUTE_CLOSED": "una carretera de su ruta habitual está cerrada",
        "CALL_IN": "su gerente le pide cubrir una función crítica",
        "SHIFT_NOT_AFFECTED": "su turno está fuera del horario afectado",
    },
}
LABELS = {
    "en": {"applies": "This applies to you because {why}.", "fyi": "For your information: {why}.",
           "todo": "What to do: {instruction}.", "access": "Building access: {access}.",
           "ack": "Please confirm you got this: {url}", "sign": "Closure Call, {facility}"},
    "es": {"applies": "Esto aplica a usted porque {why}.", "fyi": "Para su información: {why}.",
           "todo": "Qué hacer: {instruction}.", "access": "Acceso al edificio: {access}.",
           "ack": "Confirme que recibió este mensaje: {url}", "sign": "Closure Call, {facility}"},
}
ACCESS_TEXT = {
    "en": {"allowed": "open to you", "denied": "closed to you", "allowed_with_escort": "open to you with an escort",
           "allowed_after": "open to you from {time}", "allowed_until": "open to you until {time}"},
    "es": {"allowed": "abierto para usted", "denied": "cerrado para usted",
           "allowed_with_escort": "abierto para usted con escolta",
           "allowed_after": "abierto para usted desde las {time}", "allowed_until": "abierto para usted hasta las {time}"},
}
SUBJECT = {
    "en": {"initial": "{facility}: {label} on {date}", "update": "Update: {facility}, {label} on {date}",
           "all_clear": "All clear: {facility} on {date}", "call_in": "Call-in request: {facility} on {date}"},
    "es": {"initial": "{facility}: {label} el {date}", "update": "Actualización: {facility}, {label} el {date}",
           "all_clear": "Todo despejado: {facility} el {date}", "call_in": "Solicitud de presentarse: {facility} el {date}"},
}

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF]")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_NUMBER = re.compile(r"\d+")
_PUNCT = re.compile(r"[\s.,;:!?¡¿()\"'«»\-]+")


def access_text(access: Access, lang: str) -> str:
    lang = lang if lang in ACCESS_TEXT else "en"
    return ACCESS_TEXT[lang][access.status].format(time=access.time or "")


@dataclass
class MessageFacts:
    """Everything a message may say about one person. Drafts are validated against this."""
    name: str
    language: str
    facility_name: str
    operating_date: date
    posture: str
    delay_hours: int
    message_type: str  # action_required | fyi | call_in
    change: str  # initial | update | all_clear
    instruction: str
    access: Access
    why_codes: list[str]
    requires_ack: bool = False
    ack_url: str | None = None
    update_line: str | None = None
    render: frozenset[str] = frozenset()
    extra_times: list[str] = field(default_factory=list)

    @property
    def lang(self) -> str:
        return self.language if self.language in ("en", "es") else "en"

    @property
    def date_text(self) -> str:
        return fmt_date(self.operating_date, self.lang)

    @property
    def why(self) -> str:
        phrases = [WHY[self.lang][c] for c in self.why_codes if c in WHY[self.lang]]
        out: list[str] = []
        for p in phrases:
            if p not in out:
                out.append(p)
        return {"en": " and ", "es": " y "}[self.lang].join(out[:2]) or WHY[self.lang]["SCHEDULED_ONSITE"]

    def placeholders(self) -> dict[str, str]:
        """What a drafted template may reference."""
        lab = LABELS[self.lang]
        return {
            "name": self.name.split()[0],
            "why": self.why,
            "instruction": self.instruction,
            "access": access_text(self.access, self.lang),
            "ack_line": lab["ack"].format(url=self.ack_url) if self.requires_ack and self.ack_url else "",
            "update_line": self.update_line or "",
            "facility": self.facility_name,
            "date": self.date_text,
        }

    def allowed_times(self) -> set[str]:
        times = {self.access.time} | set(self.extra_times)
        times |= {f"{h.zfill(2)}:{m}" for h, m in _TIME.findall(self.instruction + " " + (self.update_line or ""))}
        return {t for t in times if t}

    def allowed_numbers(self) -> set[str]:
        blob = json.dumps(self.placeholders(), ensure_ascii=False) + " ".join(
            [str(self.delay_hours), str(self.operating_date.year), str(self.operating_date.day)] + list(self.allowed_times()))
        return {n.lstrip("0") or "0" for n in _NUMBER.findall(blob)}


def subject_for(f: MessageFacts) -> str:
    kind = "call_in" if f.message_type == "call_in" else f.change if f.change in ("update", "all_clear") else "initial"
    return SUBJECT[f.lang][kind].format(facility=f.facility_name, label=ins.posture_label(f.posture, f.lang),
                                        date=f.date_text)


def template(f: MessageFacts) -> tuple[str, str]:
    """Deterministic message. Always valid."""
    lang, ph, lab = f.lang, f.placeholders(), LABELS[f.lang]
    what = (WHAT_HAPPENED_PLAIN if "plain_language" in f.render else WHAT_HAPPENED)[lang][f.posture].format(
        facility=f.facility_name, date=f.date_text, delay_hours=f.delay_hours)
    lines = [f"{ph['name']}:"]
    if f.update_line:
        lines.append(f.update_line)
    if f.change == "all_clear":
        lines += [what, f.instruction[:1].upper() + f.instruction[1:] + "."]
    else:
        lines.append(what)
        lines.append((lab["applies"] if f.message_type in ("action_required", "call_in") else lab["fyi"]).format(why=ph["why"]))
        lines.append(lab["todo"].format(instruction=f.instruction))
        lines.append(lab["access"].format(access=ph["access"]))
    if ph["ack_line"]:
        lines.append(ph["ack_line"])
    lines.append(lab["sign"].format(facility=f.facility_name))
    return subject_for(f), "\n".join(lines)


class _Strict(dict):
    def __missing__(self, key):
        raise KeyError(f"unknown placeholder {{{key}}}")


def render_draft(subject: str, body: str, f: MessageFacts) -> tuple[str, str]:
    """Fill a drafted group template with this person's facts. Raises on unknown placeholders."""
    ph = _Strict(f.placeholders())
    text = body.format_map(ph)
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if f.requires_ack and ph["ack_line"] and f.ack_url not in text:
        text += "\n" + ph["ack_line"]
    return subject.format_map(ph).strip()[:200] or subject_for(f), text


def _norm(s: str) -> str:
    return _PUNCT.sub(" ", s.lower()).strip()


def validate(body: str, f: MessageFacts) -> str | None:
    """Reason a message is unacceptable, or None. Templates always pass."""
    if not body.strip():
        return "empty message"
    if len(body) > 1500:
        return "message too long"
    if _EMOJI.search(body):
        return "contains emoji"
    if f.change != "all_clear" and _norm(f.instruction) not in _norm(body):
        return "does not contain the duty instruction"
    times = {f"{h.zfill(2)}:{m}" for h, m in _TIME.findall(body)}
    bad = sorted(times - f.allowed_times())
    if bad:
        return f"times not in the computed values: {', '.join(bad)}"
    allowed = f.allowed_numbers()
    unknown = sorted({n for n in _NUMBER.findall(_TIME.sub(" ", body)) if (n.lstrip("0") or "0") not in allowed})
    if unknown:
        return f"numbers not in the facts: {', '.join(unknown)}"
    if f.requires_ack and f.ack_url and f.ack_url not in body:
        return "missing the acknowledgment link"
    return None


def html_body(body: str, render: frozenset[str]) -> str | None:
    """Large-text email: same words, bigger type. Screen-reader friendly: plain paragraphs."""
    if "large_text" not in render:
        return None
    paras = "".join(f"<p>{html.escape(line)}</p>" for line in body.splitlines())
    return f'<div style="font-size:22px;line-height:1.5;font-family:Arial,sans-serif">{paras}</div>'


# ---------------------------------------------------------------- role messages (English)

def supervisor_summary(name: str, facility: str, day: date, posture: str, rows: list[dict],
                       suggestions: list[dict], board_url: str) -> tuple[str, str]:
    act = [r for r in rows if r["message_type"] == "action_required"]
    fyi = [r for r in rows if r["message_type"] == "fyi"]
    lines = [f"{name.split()[0]}: team summary for {fmt_date(day, 'en')} ({ins.posture_label(posture)}).",
             f"{len(act)} need to act, {len(fyi)} FYI, {len(rows) - len(act) - len(fyi)} no change."]
    emergency = [r["name"] for r in act if "EMERGENCY_DUTY" in r["codes"]]
    if emergency:
        lines.append(f"Emergency staff: {', '.join(emergency)}.")
    acc = sum(1 for r in rows if "ACCOMMODATION_ON_FILE" in r["codes"])
    if acc:
        lines.append(f"Accommodation on file: {acc} (handled automatically).")
    for s in suggestions:
        lines.append(f"{s['name']}: {s['audience_text']}. Suggestion: {s['suggestion']}.")
    lines.append(f"Team board: {board_url}")
    return f"Team summary: {facility}, {fmt_date(day, 'en')}", "\n".join(lines)


def access_list_message(facility: str, day: date, posture: str, rows: list[dict], url: str) -> tuple[str, str]:
    lines = [f"Building access for {fmt_date(day, 'en')} ({ins.posture_label(posture)}):"]
    for r in rows:
        lines.append(f"- {r['name']}: {r['access']} ({r['window']}) [{r['code']}]")
    lines.append(f"Full list: {url}")
    return f"Access list: {facility}, {fmt_date(day, 'en')}", "\n".join(lines)


def staffing_alert_message(facility: str, day: date, items: list[dict], url: str) -> tuple[str, str]:
    lines = [f"Staffing check for {fmt_date(day, 'en')}:"]
    for it in items:
        lines.append(f"- {it['label']}" + (f"; backups: {', '.join(b['name'] for b in it['backups'])}"
                                            if it["backups"] else ""))
    lines.append(f"Pick call-ins: {url}")
    return f"Staffing shortfall: {facility}, {fmt_date(day, 'en')}", "\n".join(lines)

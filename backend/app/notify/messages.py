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
from datetime import date, datetime
from zoneinfo import ZoneInfo

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
    hazard: str = "No active hazard identified"
    hazard_source: str = "Facility decision"
    hazard_end: str = ""
    footer: str = ""
    report_time: str = ""
    end_time: str = ""
    function_name: str = ""
    timezone: str = "America/Chicago"

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
        values = {
            "name": self.name.split()[0],
            "why": self.why,
            "instruction": self.instruction,
            "access": access_text(self.access, self.lang),
            "ack_line": lab["ack"].format(url=self.ack_url) if self.requires_ack and self.ack_url else "",
            "update_line": self.update_line or "",
            "facility": self.facility_name,
            "date": self.date_text,
        }
        values.update(what_happened=what_happened(self), personal_action=personal_action(self),
                      access_line=building_line(self), reply_line=reply_line(self), footer=self.footer)
        return values

    def allowed_times(self) -> set[str]:
        times = {self.access.time, self.report_time, self.end_time} | set(self.extra_times)
        times |= {f"{h.zfill(2)}:{m}" for h, m in _TIME.findall(self.hazard_end)}
        times |= {f"{h.zfill(2)}:{m}" for h, m in _TIME.findall(self.instruction + " " + (self.update_line or ""))}
        return {t for t in times if t}

    def allowed_numbers(self) -> set[str]:
        blob = json.dumps(self.placeholders(), ensure_ascii=False) + " ".join(
            [str(self.delay_hours), str(self.operating_date.year), str(self.operating_date.day)] + list(self.allowed_times()))
        return {n.lstrip("0") or "0" for n in _NUMBER.findall(blob)}


def subject_for(f: MessageFacts) -> str:
    hazard = f.hazard if f.hazard != "No active hazard identified" else ("Update" if f.lang == "en" else "Aviso")
    labels = {"normal": "open", "delayed_arrival": "late opening", "early_dismissal": "early dismissal",
              "telework": "telework", "closure": "closed"}
    es_labels = {"normal": "abierto", "delayed_arrival": "apertura tardía", "early_dismissal": "salida anticipada",
                 "telework": "teletrabajo", "closure": "cerrado"}
    if "LEAVE" in f.why_codes or "DAY_OFF" in f.why_codes:
        action = "sin turno" if f.lang == "es" else "no shift"
    elif f.message_type == "call_in" or "EMERGENCY_DUTY" in f.why_codes or "FACILITY_OPS" in f.why_codes:
        action = ("report " if f.lang == "en" else "preséntese ") + (f.report_time or ("según horario" if f.lang == "es" else "as scheduled"))
    elif f.posture == "early_dismissal" and _TIME.search(f.instruction):
        action = ("leave " if f.lang == "en" else "salga ") + _TIME.search(f.instruction).group()
    else:
        action = "work remotely" if "telework" in f.instruction or "distancia" in f.instruction else "usual schedule"
        if f.posture == "closure" and "follow agency leave policy" in f.instruction:
            action = "stay home"
        elif f.posture == "closure" and "política de licencias" in f.instruction:
            action = "stay home"
        elif "request unscheduled leave" in f.instruction or "licencia no programada" in f.instruction:
            action = "report or request leave"
        elif f.posture == "delayed_arrival" and _TIME.search(f.instruction):
            action = "report " + _TIME.search(f.instruction).group()
        if f.lang == "es":
            action = {"work remotely": "trabaje remotamente", "usual schedule": "horario habitual",
                      "stay home": "quédese en casa", "report or request leave": "preséntese o solicite licencia"}.get(action, action.replace("report ", "preséntese "))
    label = (es_labels if f.lang == "es" else labels)[f.posture]
    today = ("hoy" if f.lang == "es" else "today") if f.operating_date == datetime.now(ZoneInfo(f.timezone)).date() else f.date_text.split(",")[0].split()[0]
    # A connected dash saves a word while keeping the hazard and action readable.
    return f"{hazard} - {label} {today}; {action}" if len(f"{hazard} - {label} {today}; {action}".split()) < 10 else f"{hazard}-{label} {today}; {action}"


def what_happened(f: MessageFacts) -> str:
    label = ins.posture_label(f.posture, f.lang)
    until = (" hasta " if f.lang == "es" else " until ") + f.hazard_end if f.hazard_end else ""
    hazard = {"Flood Warning": "Aviso de inundación", "Flash Flood Warning": "Aviso de inundación repentina",
              "Winter Storm Warning": "Aviso de tormenta invernal", "Ice Storm Warning": "Aviso de tormenta de hielo",
              "High Wind Warning": "Aviso de vientos fuertes", "Wind Advisory": "Alerta de viento",
              "Tornado Warning": "Aviso de tornado", "Severe Thunderstorm Warning": "Aviso de tormenta severa"}.get(f.hazard, f.hazard) if f.lang == "es" else f.hazard
    prefix = "Actualización: " if f.lang == "es" and f.change == "update" else "Update: " if f.change == "update" else ""
    return f"{prefix}{f.facility_name}: {label}, {f.date_text}. {f.hazard_source}: {hazard}{until}."


def personal_action(f: MessageFacts) -> str:
    """Presentation only: preserve the computed duty, including every exception/option."""
    es = f.lang == "es"
    time = f.report_time or ("su hora programada" if es else "your scheduled time")
    if "DETAIL_OUT" in f.why_codes:
        return f.instruction[:1].upper() + f.instruction[1:] + "."
    if f.message_type == "call_in":
        return ((f"Su gerente le pide cubrir {f.function_name or 'una función crítica'}; preséntese en persona a las {time}." if es
                 else f"Your manager called you in for {f.function_name or 'critical coverage'}; report on site by {time}."))
    if "FACILITY_OPS" in f.why_codes:
        return (f"Tiene tareas de operaciones del edificio: {f.instruction}. Turno: {time}." if es else
                f"You have facility operations duty: {f.instruction}. Shift: {time}.")
    if "EMERGENCY_DUTY" in f.why_codes:
        function = f.function_name or ("su función de emergencia" if es else "your emergency role")
        if es:
            function = {"animal care": "cuidado de animales", "lab freezer monitoring": "vigilancia de congeladores",
                        "security": "seguridad", "it operations": "operaciones informáticas"}.get(function, function)
        if f.posture == "early_dismissal":
            return (f"Es personal de emergencia para {function}; permanezca de servicio desde las {time} salvo indicación contraria." if es else
                    f"You are emergency staff for {function}; stay on duty from {time} unless told otherwise.")
        return (f"Es personal de emergencia para {function}; preséntese en persona a las {time} salvo indicación contraria." if es else
                f"You are emergency staff for {function}, so still report on site at {time} unless told otherwise.")
    if f.change == "all_clear":
        return (f"El aviso anterior ya no aplica; siga su horario desde las {time}." if es else
                f"The earlier notice no longer applies; follow your schedule from {time}.")
    if "LEAVE" in f.why_codes or "DAY_OFF" in f.why_codes:
        return "No tiene turno este día; no venga." if es else "You are not scheduled this day; don't come in."
    if "CONTRACTOR_STATUS_ONLY" in f.why_codes:
        return f.instruction[:1].upper() + f.instruction[1:] + "."
    if "SHIFT_NOT_AFFECTED" in f.why_codes:
        return (f"Su turno no está afectado; siga su horario desde las {time}." if es else
                f"Your shift is outside the affected hours; work your schedule from {time}.")
    if f.instruction in ("telework today", "office closed, telework", "no change for you: keep teleworking",
                         "trabaje a distancia hoy", "oficina cerrada, trabaje a distancia", "sin cambios para usted: siga trabajando a distancia"):
        return (f"No venga; trabaje desde casa desde las {time}." if es else f"Don't come in; work from home from {time}.")
    if f.posture == "normal":
        return (f"Siga su horario habitual desde las {time}." if es else f"Work your normal schedule from {time}.")
    if f.instruction in ("office closed, follow agency leave policy", "oficina cerrada, siga la política de licencias de la agencia"):
        return (f"No venga a las {time}; siga la política de licencias de la agencia." if es else
                f"Don't come in at {time}; follow agency leave policy.")
    if f.instruction in ("report, or request unscheduled leave", "preséntese o solicite licencia no programada"):
        return (f"Preséntese a las {time} o solicite licencia no programada." if es else
                f"Report at {time}, or request unscheduled leave.")
    return f.instruction[:1].upper() + f.instruction[1:] + "."


def building_line(f: MessageFacts) -> str:
    if any(c in f.why_codes for c in ("TELEWORK_SCHEDULED", "LEAVE", "DAY_OFF", "DETAIL_OUT")):
        return ""
    if f.access.status == "allowed" and not any(c in f.why_codes for c in ("EMERGENCY_DUTY", "FACILITY_OPS", "CALL_IN")):
        return ""
    return ("Acceso al edificio: " if f.lang == "es" else "Building access: ") + access_text(f.access, f.lang) + "."


def reply_line(f: MessageFacts) -> str:
    if f.requires_ack and f.ack_url:
        return ("Confirme: " if f.lang == "es" else "Please confirm: ") + f.ack_url
    return "No necesita responder." if f.lang == "es" else "No reply needed."


def template(f: MessageFacts) -> tuple[str, str]:
    """Deterministic message. Always valid."""
    lines = [what_happened(f), f"**{personal_action(f)}**", building_line(f), reply_line(f), f.footer]
    return subject_for(f), "\n".join(line for line in lines if line)


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
    return subject_for(f), text  # hazard/action subjects are deterministic, never model-written


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
    if _norm(personal_action(f)) not in _norm(body):
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
    expected = template(f)[1].splitlines()
    if body.splitlines() != expected:
        return "must use the five-line hazard, personal action, access, reply and footer structure"
    return None


def html_body(body: str, render: frozenset[str]) -> str | None:
    """Large-text email: same words, bigger type. Screen-reader friendly: plain paragraphs."""
    if "text_only" in render or ("large_text" not in render and "**" not in body):
        return None
    paras = "".join("<p>" + re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html.escape(line)) + "</p>" for line in body.splitlines())
    size = 22 if "large_text" in render else 16
    return f'<div style="font-size:{size}px;line-height:1.5;font-family:Arial,sans-serif">{paras}</div>'


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

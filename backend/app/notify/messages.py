"""Plain-language notification text, English and Spanish."""
from datetime import date

from ..models import Employee, Facility

_POSTURE = {
    "en": {
        "normal": "{facility} is operating normally on {date}. No change to your schedule.",
        "delayed_arrival": "{facility} has a DELAYED ARRIVAL on {date} because of weather. Do not travel "
                           "during the usual morning commute. Your supervisor will confirm your reporting time.",
        "early_dismissal": "{facility} is DISMISSING EARLY on {date} because of weather. Plan to leave early "
                           "and travel only when it is safe. Your supervisor will confirm the release time.",
        "telework": "{facility} is TELEWORK ONLY on {date} because of weather. Do not travel to the office.",
        "closure": "{facility} is CLOSED on {date} because of weather. Do not report to the office.",
    },
    "es": {
        "normal": "{facility} opera con normalidad el {date}. No hay cambios en su horario.",
        "delayed_arrival": "{facility} tiene LLEGADA RETRASADA el {date} por el clima. No viaje durante el "
                           "horario habitual de la mañana. Su supervisor confirmará su hora de entrada.",
        "early_dismissal": "{facility} tendrá SALIDA ANTICIPADA el {date} por el clima. Prepárese para salir "
                           "temprano y viaje solo cuando sea seguro. Su supervisor confirmará la hora de salida.",
        "telework": "{facility} estará en TELETRABAJO SOLAMENTE el {date} por el clima. No viaje a la oficina.",
        "closure": "{facility} está CERRADA el {date} por el clima. No se presente a la oficina.",
    },
}

_EXTRA = {
    "en": {
        "applies": "This applies to you.",
        "telework_ok": "You are telework-eligible: please work from home.",
        "telework_no": "You are not telework-eligible: contact your supervisor about leave options.",
        "essential": "You are designated ESSENTIAL: your reporting requirement is unchanged unless your "
                     "supervisor tells you otherwise.",
        "early_notice": "You are receiving early notice so you have extra time to plan your travel.",
        "temp": "You are receiving this because you are temporarily assigned to this office.",
    },
    "es": {
        "applies": "Esto aplica a usted.",
        "telework_ok": "Usted es elegible para teletrabajo: trabaje desde casa.",
        "telework_no": "Usted no es elegible para teletrabajo: comuníquese con su supervisor sobre opciones de licencia.",
        "essential": "Usted está designado como ESENCIAL: su obligación de presentarse no cambia a menos que "
                     "su supervisor le indique lo contrario.",
        "early_notice": "Recibe este aviso con anticipación para que tenga más tiempo de planificar su viaje.",
        "temp": "Recibe este mensaje porque está asignado temporalmente a esta oficina.",
    },
}


def draft_message(emp: Employee, facility: Facility, posture: str, day: date,
                  temp_here: bool, early_notice: bool) -> str:
    lang = emp.language if emp.language in _POSTURE else "en"
    x = _EXTRA[lang]
    lines = [f"{emp.name}: {x['applies']}",
             _POSTURE[lang][posture].format(facility=facility.name, date=day.isoformat())]
    if posture == "telework" and not emp.essential:
        lines.append(x["telework_ok"] if emp.telework_eligible else x["telework_no"])
    if emp.essential:
        lines.append(x["essential"])
    if temp_here:
        lines.append(x["temp"])
    if early_notice:
        lines.append(x["early_notice"])
    lines.append("- Closure Call")
    return "\n".join(lines)

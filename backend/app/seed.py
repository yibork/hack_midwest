"""Synthetic demo data: one Kansas City facility, 40 fake people, critical functions, shifts.

Covers every reason code: detail in/out, telework, leave, day off, early and night shifts,
emergency staff for each critical function, facility ops, security desk, contractors (one in a
critical function), three supervisors with teams, each accommodation type, Spanish speakers,
homes in eight counties on both sides of the state line, and varied commute routes.

Animal care is deliberately one short on closure (min 2; one emergency tech is on leave and
another is on detail), with exactly one cross-trained backup, so the shortfall flow shows.

Home zones are resolved once here via NWS /points (RESOLVE_HOME_ZONES=0 skips the network and uses
the codes below, which were resolved the same way).

Run directly with `python -m app.seed`; the app also calls seed() on startup. Idempotent.
"""
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .models import Assignment, CriticalFunction, Employee, Facility, Shift

FACILITY_ID = "usda-kc-demo"
TZ = "America/Chicago"

# Home locations: (lat, lon, NWS forecast zone, NWS county) as returned by /points.
HOMES = {
    "kc_mo": (39.0997, -94.5786, "MOZ037", "MOC095"),
    "independence": (39.0911, -94.4155, "MOZ037", "MOC095"),
    "lees_summit": (38.9108, -94.3822, "MOZ037", "MOC095"),
    "liberty": (39.2461, -94.4191, "MOZ029", "MOC047"),
    "gladstone": (39.2039, -94.5547, "MOZ029", "MOC047"),
    "platte_city": (39.37, -94.7825, "MOZ028", "MOC165"),
    "belton": (38.812, -94.5319, "MOZ043", "MOC037"),
    "harrisonville": (38.6533, -94.3488, "MOZ043", "MOC037"),
    "overland_park": (38.9822, -94.6708, "KSZ105", "KSC091"),
    "olathe": (38.8814, -94.8191, "KSZ105", "KSC091"),
    "kc_ks": (39.1142, -94.6275, "KSZ104", "KSC209"),
    "leavenworth": (39.3111, -94.9225, "KSZ103", "KSC103"),
    "paola": (38.5722, -94.8791, "KSZ057", "KSC121"),
    "excelsior": (39.3392, -94.2261, "MOZ029", "MOC047"),
    "lawrence": (38.9717, -95.2353, "KSZ040", "KSC045"),
}

SHIFTS = {"day": ("07:30", "16:00"), "early": ("05:00", "13:30"), "night": ("22:00", "06:00"),
          "short": ("07:30", "13:00")}

FUNCTIONS = [
    ("animal_care", "Animal care", {"normal": 2, "delayed_arrival": 2, "early_dismissal": 2, "telework": 2, "closure": 2},
     "Animals must be fed and checked twice a day, every day."),
    ("lab_freezer_monitoring", "Lab freezer monitoring", {p: 1 for p in ("normal", "delayed_arrival", "early_dismissal", "telework", "closure")},
     "-80C freezers hold irreplaceable samples; alarms need a person onsite."),
    ("security", "Security", {"normal": 2, "delayed_arrival": 2, "early_dismissal": 2, "telework": 1, "closure": 1},
     "Security desk staffed whenever anyone is in the building."),
    ("it_ops", "IT operations", {"normal": 1, "delayed_arrival": 1, "early_dismissal": 1, "telework": 1, "closure": 0},
     "Network and badge systems; remote support is enough on closure days."),
]

# key, name, role, designation, supervisor key, functions, primary function, accommodation needs,
# language, preferred channels, ntfy topic, home, commute minutes, routes, access level,
# status today, shift, assignment: None | ("detail_out"|"detail_in", other facility, days)
P = None
ROSTER = [
    ("mgr", "Morgan Ellery", "facility_manager", "emergency", P, [], P, [], "en", ["email", "push"], P, "overland_park", 30, ["I-435", "I-35"], "emergency", "onsite", "day", P),
    ("sup_animal", "Dana Reyes", "supervisor", "emergency", "mgr", [], P, [], "en", ["push", "email"], P, "lees_summit", 40, ["I-470", "I-435"], "emergency", "onsite", "early", P),
    ("sup_ops", "Kwame Asante", "supervisor", "telework_ready", "mgr", ["it_ops"], "it_ops", [], "en", ["email", "push"], P, "liberty", 30, ["I-35", "M-291"], "standard", "onsite", "day", P),
    ("sup_admin", "Lucía Marín", "supervisor", "telework_ready", "mgr", [], P, [], "es", ["push", "email"], P, "kc_ks", 20, ["I-70", "I-670"], "standard", "onsite", "day", P),
    # Animal care and lab (Dana's team)
    ("ava", "Ava Brennan", "employee", "emergency", "sup_animal", ["animal_care"], "animal_care", [], "en", ["push", "email"], "closurecall-demo-emergency", "independence", 25, ["I-70", "I-435"], "emergency", "onsite", "day", P),
    ("tomas", "Tomás Quiroga", "employee", "emergency", "sup_animal", ["animal_care"], "animal_care", [], "es", ["push", "email"], P, "kc_mo", 15, ["US-71"], "emergency", "leave", "day", P),
    ("marcus", "Marcus Bell", "employee", "emergency", "sup_animal", ["animal_care"], "animal_care", [], "en", ["push", "email"], P, "gladstone", 20, ["I-29", "I-35"], "emergency", "onsite", "day", ("detail_out", "usda-omaha-demo", 9)),
    ("jordan", "Jordan Pike", "employee", "telework_ready", "sup_animal", ["lab_freezer_monitoring", "animal_care"], "lab_freezer_monitoring", [], "en", ["email", "push"], P, "gladstone", 15, ["I-29", "I-35"], "standard", "onsite", "day", P),
    ("priya", "Priya Natarajan", "employee", "emergency", "sup_animal", ["lab_freezer_monitoring"], "lab_freezer_monitoring", [], "en", ["push", "email"], P, "platte_city", 35, ["I-29"], "emergency", "onsite", "night", P),
    ("rhea", "Rhea Sandoval", "employee", "non_telework", "sup_animal", [], P, ["mobility_early_dismissal"], "en", ["push", "email"], P, "belton", 30, ["US-71", "I-49"], "standard", "onsite", "day", P),
    ("hannah", "Hannah Kim", "employee", "telework_ready", "sup_animal", [], P, [], "en", ["email"], P, "platte_city", 35, ["I-29"], "standard", "onsite", "day", ("detail_out", "usda-stl-demo", 10)),
    ("aisha", "Aisha Rahman", "employee", "telework_ready", "sup_animal", [], P, [], "en", ["email", "push"], P, "olathe", 35, ["I-35"], "standard", "onsite", "day", P),
    ("zoe", "Zoe Adams", "employee", "telework_ready", "sup_animal", [], P, [], "en", ["email", "push"], P, "lawrence", 55, ["K-10", "I-435"], "standard", "onsite", "day", P),
    # Operations, IT and security (Kwame's team)
    ("kenji", "Kenji Watanabe", "employee", "emergency", "sup_ops", ["it_ops"], "it_ops", [], "en", ["push", "email"], P, "olathe", 35, ["I-35"], "emergency", "onsite", "day", P),
    ("hank", "Hank Oduya", "facility_ops", "emergency", "sup_ops", [], P, [], "en", ["push", "email"], P, "kc_mo", 15, ["US-71"], "facility_ops", "onsite", "early", P),
    ("selena", "Selena Brooks", "facility_ops", "emergency", "sup_ops", [], P, [], "en", ["push", "email"], P, "independence", 25, ["I-70"], "facility_ops", "onsite", "night", P),
    ("rafael", "Rafael Ortiz", "security_desk", "emergency", "sup_ops", ["security"], "security", [], "es", ["push", "email"], P, "kc_mo", 15, ["US-71"], "emergency", "onsite", "day", P),
    ("tina", "Tina Walsh", "security_desk", "emergency", "sup_ops", ["security"], "security", [], "en", ["push", "email"], P, "kc_ks", 20, ["I-70"], "emergency", "onsite", "night", P),
    ("grace", "Grace Okafor", "employee", "telework_ready", "sup_ops", [], P, ["early_notice"], "en", ["email"], P, "leavenworth", 50, ["K-7", "I-435"], "standard", "onsite", "day", P),
    ("martin", "Martín Esquivel", "employee", "non_telework", "sup_ops", [], P, ["plain_language"], "es", ["push", "email"], P, "kc_ks", 20, ["I-70"], "standard", "onsite", "early", P),
    ("helen", "Helen Brandt", "employee", "telework_ready", "sup_ops", [], P, ["screen_reader"], "en", ["email"], P, "lawrence", 50, ["K-10", "I-435"], "standard", "onsite", "day", P),
    ("victor", "Victor Lindqvist", "employee", "telework_ready", "sup_ops", [], P, ["large_text"], "en", ["email"], P, "liberty", 30, ["I-35"], "standard", "onsite", "day", P),
    ("deshawn", "Deshawn Carter", "employee", "non_telework", "sup_ops", [], P, ["text_only"], "en", ["push", "email"], P, "independence", 25, ["I-70"], "standard", "onsite", "day", P),
    ("ignacio", "Ignacio Rivera", "employee", "telework_ready", "sup_ops", [], P, [], "es", ["push", "email"], P, "excelsior", 45, ["US-69", "I-35"], "standard", "telework", "day", P),
    ("corwin", "Corwin Blackwood", "employee", "non_telework", "sup_ops", [], P, [], "en", ["email", "push"], P, "independence", 30, ["I-70"], "standard", "onsite", "day", ("detail_in", "usda-stl-demo", 5)),
    ("paul", "Paul Jensen", "employee", "non_telework", "sup_ops", [], P, [], "en", ["email", "push"], P, "paola", 50, ["US-169", "I-435"], "standard", "onsite", "day", P),
    ("liam", "Liam O'Connor", "employee", "non_telework", "sup_ops", [], P, [], "en", ["push", "email"], P, "belton", 30, ["US-71"], "standard", "onsite", "early", P),
    ("gus", "Gus Petrakis", "contractor", "non_telework", "sup_ops", ["it_ops"], "it_ops", [], "en", ["email", "push"], P, "overland_park", 25, ["I-435"], "contractor_badge", "onsite", "day", P),
    ("nadia", "Nadia Kowalski", "contractor", "non_telework", "sup_ops", [], P, [], "en", ["push", "email"], P, "kc_ks", 20, ["I-70"], "contractor_badge", "onsite", "early", P),
    ("luis", "Luis Herrera", "contractor", "non_telework", "sup_ops", [], P, [], "es", ["push", "email"], P, "kc_mo", 15, ["US-71"], "escort_required", "onsite", "day", P),
    # Admin and inspection (Lucía's team)
    ("eli", "Eli Navarro", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["push", "email"], "closurecall-demo-exposed", "harrisonville", 45, ["I-49", "US-71"], "standard", "onsite", "day", P),
    ("sam", "Sam Whitaker", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["push", "email"], "closurecall-demo-telework", "overland_park", 25, ["I-435", "US-69"], "standard", "onsite", "day", P),
    ("noor", "Noor Haddad", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["push", "email"], "closurecall-demo-teleworking", "olathe", 35, ["I-35"], "standard", "telework", "day", P),
    ("mei", "Mei Chen", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["push", "email"], P, "lees_summit", 35, ["I-470", "US-50"], "standard", "leave", "day", P),
    ("rosa", "Rosa Delgado", "employee", "non_telework", "sup_admin", [], P, [], "es", ["push", "email"], P, "kc_mo", 15, ["US-71"], "standard", "day_off", P, P),
    ("owen", "Owen Fitzgerald", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["email", "push"], P, "gladstone", 20, ["I-35"], "standard", "day_off", P, P),
    ("fatima", "Fatima Diallo", "employee", "telework_ready", "sup_admin", [], P, [], "en", ["email", "push"], P, "overland_park", 25, ["I-435"], "standard", "telework", "day", P),
    ("ximena", "Ximena Albarrán", "employee", "telework_ready", "sup_admin", [], P, [], "es", ["push", "email"], P, "kc_mo", 20, ["I-35"], "standard", "onsite", "day", ("detail_in", "usda-wichita-demo", 14)),
    ("ben", "Ben Turner", "employee", "non_telework", "sup_admin", [], P, [], "en", ["push", "email"], P, "leavenworth", 55, ["K-7"], "standard", "onsite", "short", P),
    ("carla", "Carla Mendes", "employee", "telework_ready", "sup_admin", [], P, [], "es", ["push", "email"], P, "kc_ks", 20, ["I-70", "I-635"], "standard", "onsite", "day", P),
]

PERSONAS = {
    "ava": "Emergency, animal care: must report and acknowledge on closure days",
    "sam": "Telework-ready, scheduled onsite: told to telework or report later",
    "noor": "Already teleworking: FYI by email only, never a push",
    "eli": "Home under a warning while the office is normal: FYI, supervisor informed",
}

log = logging.getLogger(__name__)
_ready = False
_lock = threading.Lock()


def emp_id(key: str) -> str:
    return f"emp-{[r[0] for r in ROSTER].index(key) + 1:03d}"


def _email(name: str) -> str:
    import unicodedata
    slug = unicodedata.normalize("NFKD", name.lower().replace(" ", ".").replace("'", "")).encode("ascii", "ignore").decode()
    return f"{slug}@example.com"


def _resolve_zones() -> dict[str, tuple[str | None, str | None]]:
    """Home zone and county per location: /points once per unique home (cached), else the table."""
    out = {k: (v[2], v[3]) for k, v in HOMES.items()}
    if not settings.resolve_home_zones:
        return out
    from .sources import nws
    for key, (lat, lon, _, _) in HOMES.items():
        info, ev = nws.get_point(lat, lon)
        if info and info.get("forecastZone") and info.get("county"):
            out[key] = (info["forecastZone"], info["county"])
    return out


def ensure_seeded() -> bool:
    """seed() once. If the database is unreachable (e.g. the managed Postgres allow list doesn't
    include this host yet) keep serving; /api/health reports it and retries on the next call."""
    global _ready
    with _lock:
        if not _ready:
            try:
                seed()
                _ready = True
            except Exception as e:
                log.error("database not ready: %s", type(e).__name__)
        return _ready


def seed() -> None:
    init_db()
    today = datetime.now(ZoneInfo(TZ)).date()
    with SessionLocal() as db:
        if db.get(Facility, FACILITY_ID) is None:
            db.add(Facility(
                id=FACILITY_ID, name="USDA Kansas City Field Office (demo)",
                address="Kansas City, MO (synthetic demo facility)", lat=39.0997, lon=-94.5786,
                timezone=TZ, open_time="07:30", close_time="16:00",
                access_routes=["I-35", "I-70", "US-71"],
            ))
            db.flush()
        if not db.scalar(select(Employee).limit(1)):
            zones = _resolve_zones()
            for code, name, mins, notes in FUNCTIONS:
                db.add(CriticalFunction(facility_id=FACILITY_ID, code=code, name=name, min_onsite=mins, notes=notes))
            for (key, name, role, desig, sup, fns, primary, needs, lang, channels, topic, home, commute, routes,
                 access, _, _, assignment) in ROSTER:
                lat, lon, _, _ = HOMES[home]
                db.add(Employee(
                    id=emp_id(key), name=name, role=role, designation=desig,
                    supervisor_id=emp_id(sup) if sup else None, functions=fns, primary_function=primary,
                    accommodation_on_file=bool(needs), accommodation_needs=needs, language=lang,
                    preferred_channels=channels, ntfy_topic=topic, email=_email(name), home_lat=lat, home_lon=lon,
                    home_zone=zones[home][0], home_county=zones[home][1], commute_minutes=commute,
                    commute_routes=routes, access_level=access))
                if assignment and assignment[0] == "detail_in":
                    db.add(Assignment(employee_id=emp_id(key), facility_id=assignment[1], kind="permanent"))
                    db.add(Assignment(employee_id=emp_id(key), facility_id=FACILITY_ID, kind="detail_in",
                                      start_date=today - timedelta(days=3), end_date=today + timedelta(days=assignment[2])))
                else:
                    db.add(Assignment(employee_id=emp_id(key), facility_id=FACILITY_ID, kind="permanent"))
                if assignment and assignment[0] == "detail_out":
                    db.add(Assignment(employee_id=emp_id(key), facility_id=assignment[1], kind="detail_out",
                                      start_date=today - timedelta(days=2), end_date=today + timedelta(days=assignment[2])))
            db.commit()

        # Shifts for yesterday (overnight shifts run into today), today and tomorrow (the operating
        # date rolls to tomorrow after close).
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            have = set(db.scalars(select(Shift.employee_id).where(Shift.date == day)))
            for key, *rest in ROSTER:
                status, shift = rest[14], rest[15]
                if emp_id(key) in have:
                    continue
                start, end = SHIFTS[shift] if shift else (None, None)
                db.add(Shift(employee_id=emp_id(key), date=day, status=status, shift_start=start, shift_end=end))
        db.commit()


if __name__ == "__main__":
    seed()
    print(f"Seeded demo facility and {len(ROSTER)} people.")

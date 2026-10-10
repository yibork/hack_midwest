"""Synthetic demo data: one facility and 30 fake employees covering every notification case.

Run directly with `python -m app.seed`; the app also calls seed() on startup. Idempotent:
the facility and roster are inserted once, and today's schedule rows are added if missing.
"""
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import DailySchedule, Employee, Facility

FACILITY_ID = "usda-kc-demo"
TZ = "America/Chicago"

# (name, essential, telework_eligible, accessibility_needs, channel, language, departs,
#  today's status, temp: None | ("away", facility_id, days) | ("here", home_facility_id, days))
ROSTER = [
    ("Avery Lindqvist",    True,  False, None,                       "push",  "en", "06:15", "onsite",   None),
    ("Marisol Quintero",   True,  False, None,                       "push",  "es", "05:50", "onsite",   None),
    ("Dorian Whitfield",   True,  True,  None,                       "email", "en", "06:40", "onsite",   None),
    ("Priya Ramaswamy",    True,  False, "wheelchair user",          "push",  "en", "06:00", "onsite",   None),
    ("Theo Branford",      False, True,  None,                       "email", "en", "06:45", "onsite",   None),
    ("Lucía Ferreyra",     False, True,  None,                       "push",  "es", "06:30", "onsite",   None),
    ("Kendrick Osei",      False, True,  None,                       "push",  "en", "07:00", "onsite",   None),
    ("Hollis Vantongeren", False, False, None,                       "email", "en", "06:20", "onsite",   None),
    ("Rosalind Achterberg",False, True,  "low vision; uses screen reader", "email", "en", "06:10", "onsite", None),
    ("Mateo Villaseñor",   False, True,  None,                       "push",  "es", "06:35", "onsite",   None),
    ("Imogen Fairweather", False, True,  None,                       "email", "en", "06:50", "onsite",   None),
    ("Bashir Okonkwo",     False, False, None,                       "push",  "en", "05:45", "onsite",   None),
    ("Celeste Dunmore",    False, True,  "mobility: uses a cane",    "push",  "en", "06:05", "onsite",   None),
    ("Wendell Ashgrove",   False, True,  None,                       "email", "en", "07:05", "onsite",   None),
    ("Graciela Montaño",   False, False, None,                       "push",  "es", "06:25", "onsite",   None),
    ("Pell Harrowgate",    False, True,  None,                       "email", "en", "06:55", "onsite",   None),
    # Teleworking or on leave today
    ("Saoirse Calloway",   False, True,  None,                       "email", "en", "06:30", "telework", None),
    ("Nnamdi Eze",         False, True,  None,                       "push",  "en", "06:40", "telework", None),
    ("Esperanza Robledo",  False, True,  None,                       "push",  "es", "06:15", "telework", None),
    ("Linus Halvorsen",    True,  True,  None,                       "email", "en", "06:00", "telework", None),
    ("Odessa Pruitt",      False, True,  None,                       "email", "en", "06:45", "leave",    None),
    ("Ravi Chandrakant",   False, False, None,                       "push",  "en", "06:20", "leave",    None),
    # Assigned here, on detail elsewhere
    ("Juniper Ellsworth",  False, True,  None,                       "email", "en", "06:30", "onsite",   ("away", "usda-stl-demo", 10)),
    ("Tobias Wren",        True,  False, None,                       "push",  "en", "05:55", "onsite",   ("away", "usda-omaha-demo", 3)),
    ("Amara Delacroix",    False, True,  None,                       "email", "en", "06:35", "onsite",   ("away", "usda-wichita-demo", 21)),
    # Detail ended yesterday: counts as here again
    ("Fergus McAllister",  False, True,  None,                       "push",  "en", "06:50", "onsite",   ("away", "usda-stl-demo", -1)),
    # Temp-assigned here from elsewhere
    ("Ximena Albarrán",    False, True,  None,                       "push",  "es", "06:10", "onsite",   ("here", "usda-stl-demo", 14)),
    ("Corwin Blackwood",   True,  False, None,                       "email", "en", "06:00", "onsite",   ("here", "usda-omaha-demo", 5)),
    ("Hana Takeda",        False, True,  "hard of hearing; text only","push", "en", "06:25", "onsite",   ("here", "usda-wichita-demo", 7)),
    ("Ignatius Ferro",     False, True,  None,                       "email", "en", "07:10", "onsite",   ("here", "usda-stl-demo", 30)),
]


def _contact(name: str, channel: str) -> str:
    slug = unicodedata.normalize("NFKD", name.lower().replace(" ", ".")).encode("ascii", "ignore").decode()
    return f"{slug}@example.com" if channel == "email" else f"ntfy:{slug}"


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
            for i, (name, ess, tele, access, ch, lang, departs, _, temp) in enumerate(ROSTER, 1):
                emp = Employee(
                    id=f"emp-{i:03d}", name=name, assigned_facility_id=FACILITY_ID, essential=ess,
                    telework_eligible=tele, accessibility_needs=access, preferred_channel=ch,
                    contact=_contact(name, ch), language=lang, typical_departure_time=departs,
                )
                if temp and temp[0] == "away":
                    emp.temp_assignment_facility_id = temp[1]
                    emp.temp_assignment_until = today + timedelta(days=temp[2])
                elif temp and temp[0] == "here":
                    emp.assigned_facility_id = temp[1]
                    emp.temp_assignment_facility_id = FACILITY_ID
                    emp.temp_assignment_until = today + timedelta(days=temp[2])
                db.add(emp)
            db.commit()

        # Today's and tomorrow's schedule (the operating date rolls to tomorrow after close).
        for day in (today, today + timedelta(days=1)):
            have = set(db.scalars(select(DailySchedule.employee_id).where(DailySchedule.date == day)))
            for i, row in enumerate(ROSTER, 1):
                emp_id = f"emp-{i:03d}"
                if emp_id not in have:
                    db.add(DailySchedule(employee_id=emp_id, date=day, status=row[7]))
        db.commit()


if __name__ == "__main__":
    seed()
    print("Seeded demo facility and roster.")

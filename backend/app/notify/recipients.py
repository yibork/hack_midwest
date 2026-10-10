"""Who gets notified about a posture, and why. Every considered employee is returned,
included or not, with a reason, so the manager (and the record) can see the full picture."""
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import DailySchedule, Employee, Facility
from ..schemas import Recipient
from .messages import draft_message


def _on_temp_assignment(emp: Employee, day: date) -> bool:
    return emp.temp_assignment_facility_id is not None and (
        emp.temp_assignment_until is None or emp.temp_assignment_until >= day)


def select_recipients(db: Session, facility: Facility, posture: str, day: date) -> list[Recipient]:
    employees = db.scalars(
        select(Employee).where(or_(Employee.assigned_facility_id == facility.id,
                                   Employee.temp_assignment_facility_id == facility.id))
        .order_by(Employee.name)
    ).all()
    schedules = {s.employee_id: s.status for s in db.scalars(
        select(DailySchedule).where(DailySchedule.date == day,
                                    DailySchedule.employee_id.in_([e.id for e in employees])))}

    out: list[Recipient] = []
    for emp in employees:
        status = schedules.get(emp.id, "onsite")  # no schedule row: assume onsite (notify)
        temp_here = emp.temp_assignment_facility_id == facility.id and _on_temp_assignment(emp, day)
        included, reasons, early = True, [], False

        if emp.temp_assignment_facility_id == facility.id and not temp_here:
            continue  # temp assignment here has ended; they belong to their home office again
        if emp.assigned_facility_id == facility.id and _on_temp_assignment(emp, day) and not temp_here:
            until = emp.temp_assignment_until.isoformat() if emp.temp_assignment_until else "further notice"
            included, reasons = False, [f"on detail elsewhere ({emp.temp_assignment_facility_id}) until {until}"]
        elif status in ("telework", "leave") and posture != "normal":
            included = False
            reasons = ["already teleworking today" if status == "telework" else "on leave today"]
        elif status in ("telework", "leave"):
            included = False
            reasons = [f"{'teleworking' if status == 'telework' else 'on leave'} today; normal operations, no message needed"]
        else:
            if temp_here:
                until = emp.temp_assignment_until.isoformat() if emp.temp_assignment_until else "further notice"
                reasons.append(f"temporarily assigned here from {emp.assigned_facility_id} until {until}")
            else:
                reasons.append("assigned here, scheduled onsite")
            if emp.essential:
                reasons.append("essential: reporting requirement unchanged unless told otherwise")
            if emp.accessibility_needs:
                early = True
                reasons.append(f"early notice for accessibility needs ({emp.accessibility_needs})")

        out.append(Recipient(
            employee_id=emp.id, name=emp.name, included=included, reason="; ".join(reasons),
            channel=emp.preferred_channel, contact=emp.contact, language=emp.language,
            essential=emp.essential, early_notice=early, typical_departure_time=emp.typical_departure_time,
            message=draft_message(emp, facility, posture, day, temp_here, early) if included else None,
        ))
    # Early-notice people first so they are sent first.
    out.sort(key=lambda r: (not r.included, not r.early_notice, r.name))
    return out

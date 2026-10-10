"""Acknowledgment reminders and escalation, driven by a background loop (main.py).

No ack after 15 min -> reminder to the person; 30 min -> escalate to their supervisor
(escalation_level 1); 45 min -> escalate to the facility manager (level 2). DEMO_MODE shortens
the timers to 1/2/3 minutes. Every step writes a RecordEvent.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .. import models, record
from ..config import settings
from ..db import SessionLocal, utcnow
from .notices import deliver
from .plan import ack_url


def timers() -> tuple[timedelta, timedelta, timedelta]:
    unit = timedelta(minutes=1) if settings.demo_mode else timedelta(minutes=15)
    return unit, unit * 2, unit * 3


def loop_interval_seconds() -> int:
    return 15 if settings.demo_mode else 60


def _manager_for(db, facility_id: str) -> models.Employee | None:
    ids = select(models.Assignment.employee_id).where(models.Assignment.facility_id == facility_id,
                                                      models.Assignment.kind == "permanent")
    return db.scalar(select(models.Employee).where(models.Employee.role == "facility_manager",
                                                   models.Employee.id.in_(ids)).limit(1))


def _escalation_text(person: models.Employee, n: models.Notification, level: int, sent_local: str) -> tuple[str, str]:
    who = "supervisor" if level == 1 else "facility manager"
    return (f"No acknowledgment: {person.name}",
            f"{person.name} has not acknowledged the notice sent at {sent_local} "
            f"({n.duty_instruction}). As their {who}, please follow up directly.")


def tick(now: datetime | None = None) -> list[str]:
    """One pass. Returns the event types written (for tests and logs)."""
    now = now or utcnow()
    t_remind, t_sup, t_mgr = timers()
    events: list[str] = []
    with SessionLocal() as db:
        pending = db.scalars(select(models.Notification).where(
            models.Notification.requires_ack.is_(True), models.Notification.sent_at.is_not(None),
            models.Notification.acknowledged_at.is_(None), models.Notification.escalation_level < 2)).all()
        for n in pending:
            notice = db.get(models.Notice, n.notice_id)
            person = db.get(models.Employee, n.employee_id)
            elapsed = now - n.sent_at
            tz = ZoneInfo(db.get(models.Facility, notice.facility_id).timezone)
            sent_local = n.sent_at.astimezone(tz).strftime("%H:%M")
            base = {"notification_id": n.id, "notice_id": n.notice_id, "employee_id": person.id,
                    "minutes_since_send": int(elapsed.total_seconds() // 60)}
            if elapsed >= t_remind and n.reminder_sent_at is None:
                prefix = "Recordatorio" if person.language == "es" else "Reminder"
                results = deliver(person, n.channels_planned, f"{prefix}: {n.subject or 'Closure Call'}",
                                  f"{prefix}: {n.message}", ack=ack_url(settings.public_base_url, n.id))
                n.reminder_sent_at = now
                record.append(db, "ack.reminder_sent", {**base, "channels": {k: v["ok"] for k, v in results.items()}},
                              facility_id=notice.facility_id)
                events.append("ack.reminder_sent")
            if elapsed >= t_sup and n.escalation_level < 1:
                target = db.get(models.Employee, person.supervisor_id) if person.supervisor_id else None
                target = target or _manager_for(db, notice.facility_id)
                if target:
                    subject, body = _escalation_text(person, n, 1, sent_local)
                    deliver(target, ["push", "email"], subject, body)
                n.escalation_level, n.escalated_to = 1, target.id if target else None
                record.append(db, "ack.escalated", {**base, "level": 1, "escalated_to": n.escalated_to},
                              facility_id=notice.facility_id)
                events.append("ack.escalated.1")
            if elapsed >= t_mgr and n.escalation_level < 2:
                target = _manager_for(db, notice.facility_id)
                if target:
                    subject, body = _escalation_text(person, n, 2, sent_local)
                    deliver(target, ["push", "email"], subject, body)
                n.escalation_level, n.escalated_to = 2, target.id if target else n.escalated_to
                record.append(db, "ack.escalated", {**base, "level": 2, "escalated_to": n.escalated_to},
                              facility_id=notice.facility_id)
                events.append("ack.escalated.2")
        db.commit()
    return events

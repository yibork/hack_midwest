"""Recipient selection and dispatch. Sending requires a Decision record.

send_one() is the only function that delivers a message. Both the notify agent's send_* tools
and the deterministic dispatch() go through it, so the Decision check, the "one notification
per employee per decision" rule and the record entry are enforced in one place.
"""
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import record
from ..db import SessionLocal, utcnow
from ..models import Decision, Facility, Notification, Recommendation
from ..schemas import Recipient
from .channels import send_email, send_push
from .recipients import select_recipients

__all__ = ["select_recipients", "dispatch", "send_one", "NoDecisionError", "recipients_for_decision"]

_send_lock = threading.Lock()


class NoDecisionError(Exception):
    """Raised when something tries to send without an approval record."""


def recipients_for_decision(db: Session, decision: Decision):
    rec = db.get(Recommendation, decision.recommendation_id)
    facility = db.get(Facility, rec.facility_id)
    return facility, rec, select_recipients(db, facility, decision.final_posture, rec.operating_date)


def _title(decision: Decision, rec: Recommendation) -> str:
    return f"Closure Call: {decision.final_posture.replace('_', ' ')} ({rec.operating_date})"


def send_one(decision_id: str, recipient: Recipient, message: str | None, drafted_by: str,
             subject: str | None = None) -> Notification | None:
    """Deliver (or, for excluded employees, record the skip of) one notification.
    Returns None if this employee already has a notification for this decision."""
    with _send_lock, SessionLocal() as s:
        decision = s.get(Decision, decision_id)
        if decision is None:
            raise NoDecisionError(f"no decision {decision_id}; nothing is sent without an approval record")
        if s.scalar(select(Notification).where(Notification.decision_id == decision_id,
                                               Notification.employee_id == recipient.employee_id)):
            return None
        rec = s.get(Recommendation, decision.recommendation_id)
        delivered, sent_at = False, None
        if recipient.included:
            sent_at = utcnow()
            title = subject or _title(decision, rec)
            delivered = (send_push(title, message) if recipient.channel == "push"
                         else send_email(recipient.contact, title, message))
        n = Notification(id=uuid.uuid4().hex, decision_id=decision_id, employee_id=recipient.employee_id,
                         included=recipient.included, reason=recipient.reason, channel=recipient.channel,
                         message=message if recipient.included else None, sent_at=sent_at, delivered=delivered)
        s.add(n)
        record.append(s, "notification.sent" if recipient.included else "notification.skipped", {
            "notification_id": n.id, "decision_id": decision_id, "employee_id": recipient.employee_id,
            "name": recipient.name, "included": recipient.included, "reason": recipient.reason,
            "channel": recipient.channel, "delivered": delivered,
            "drafted_by": drafted_by if recipient.included else None,
        }, facility_id=rec.facility_id, evaluation_id=rec.evaluation_id)
        s.commit()
        return n


def dispatch(db: Session, decision_id: str) -> list[Notification]:
    """Deterministic dispatch: template message for every included employee who has not been
    notified yet, and a skip record for every excluded one. Idempotent."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NoDecisionError(f"no decision {decision_id}; nothing is sent without an approval record")
    _, _, recipients = recipients_for_decision(db, decision)
    for r in recipients:
        send_one(decision_id, r, r.message, drafted_by="template")
    db.expire_all()
    return list(db.scalars(select(Notification).where(Notification.decision_id == decision_id)))

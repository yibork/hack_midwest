"""Notify agent: writes each included employee's message in their language and sends it with
send_push / send_email. Who is included stays deterministic (select_recipients). The send tools
are guarded twice: DispatchGuard cancels them without a stored Decision, and send_one() checks
the Decision and the roster again in code. Anyone the agent didn't reach gets the template."""
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session
from strands import ToolContext, tool

from .. import record
from ..config import settings
from ..models import Decision, Employee, Notification
from ..notify import NoDecisionError, dispatch, recipients_for_decision, send_one
from ..schemas import Recipient
from .runtime import AgentUnavailable, invoke, make_agent, new_state, record_unavailable
from .writer_agent import mentioned_postures

NAME = "notify_agent"
MAX_MESSAGE_CHARS = 700

SYSTEM_PROMPT = """You write and send weather operating-status messages to USDA employees.

For EACH employee in the list:
1. Write a short, plain-language message (max 80 words) in the employee's language \
("en" = English, "es" = Spanish). Start with their name. Say that it applies to them, state the \
facility's posture for the date, and what they should do. If essential is true, say their \
reporting requirement is unchanged unless their supervisor tells them otherwise. If early_notice \
is true, say they are getting early notice to plan travel. If temp_assigned_here is true, say they \
get this because they are temporarily assigned to this office.
2. Send it with the tool matching their channel: send_push for "push", send_email for "email".

Use only the facts given. Do not mention any other posture. Send each employee exactly one message. \
Send at most 5 employees per turn (your output is limited), then continue with the next ones. \
When everyone has been sent, reply "done"."""


def _check_message(r: Recipient, message: str, posture: str) -> str | None:
    if not message or not message.strip():
        return "empty message"
    if len(message) > MAX_MESSAGE_CHARS:
        return f"message too long ({len(message)} > {MAX_MESSAGE_CHARS} characters)"
    wrong = mentioned_postures(message) - {posture}
    if wrong:
        return f"message names posture(s) {sorted(wrong)}; the decision is {posture}"
    if not re.search(re.escape(r.name.split()[0]), message, re.IGNORECASE):
        return "message must address the employee by name"
    return None


def _send(tool_context: ToolContext, employee_id: str, message: str, channel: str, subject: str | None = None) -> str:
    state = tool_context.invocation_state
    if state["closed"].is_set():
        return "Error: dispatch window closed; nothing sent."
    r: Recipient | None = state["recipients"].get(employee_id)
    if r is None or not r.included:
        return f"Error: {employee_id} is not an included recipient for this decision; nothing sent."
    if r.channel != channel:
        return f"Error: {r.name} prefers {r.channel}; use send_{r.channel}."
    problem = _check_message(r, message, state["posture"])
    if problem:
        return f"Error: {problem}. Rewrite and try again."
    n = send_one(state["decision_id"], r, message.strip(), drafted_by=NAME, subject=subject)
    if n is None:
        return f"Already sent to {r.name}; not sent again."
    return f"Sent to {r.name} via {channel} ({'delivered' if n.delivered else 'not delivered: channel not configured'})."


@tool(context=True)
def send_push(employee_id: str, message: str, tool_context: ToolContext) -> str:
    """Send a push notification to one employee. Requires an approved decision on record.

    Args:
        employee_id: The employee's id from the list.
        message: The full message text in the employee's language.
    """
    return _send(tool_context, employee_id, message, "push")


@tool(context=True)
def send_email(employee_id: str, subject: str, message: str, tool_context: ToolContext) -> str:
    """Send an email to one employee. Requires an approved decision on record.

    Args:
        employee_id: The employee's id from the list.
        subject: Short subject line in the employee's language.
        message: The full message text in the employee's language.
    """
    return _send(tool_context, employee_id, message, "email", subject=subject[:120] if subject else None)


def _facts(r: Recipient, telework_eligible: dict[str, bool]) -> dict:
    return {
        "employee_id": r.employee_id, "name": r.name, "language": r.language, "channel": r.channel,
        "applies_to_them": True, "essential": r.essential, "early_notice": r.early_notice,
        "temp_assigned_here": r.reason.startswith("temporarily assigned here"),
        "telework_eligible": telework_eligible.get(r.employee_id),
        "reference_message": r.message,  # deterministic template, for facts and tone
    }


def notify(db: Session, decision_id: str) -> list[Notification]:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NoDecisionError(f"no decision {decision_id}; nothing is sent without an approval record")
    existing = list(db.scalars(select(Notification).where(Notification.decision_id == decision_id)))
    if existing:
        return existing  # idempotent: already dispatched

    facility, rec, recipients = recipients_for_decision(db, decision)
    included = [r for r in recipients if r.included]
    state = new_state(evaluation_id=rec.evaluation_id, facility_id=facility.id, decision_id=decision_id,
                      llm_budget_key=f"notify:{decision_id}")
    state.update(recipients={r.employee_id: r for r in recipients}, posture=decision.final_posture)

    if included:
        tele = {e.id: e.telework_eligible for e in db.scalars(
            select(Employee).where(Employee.id.in_([r.employee_id for r in included])))}
        prompt = (f"Facility: {facility.name}\nDate: {rec.operating_date}\n"
                  f"Approved posture: {decision.final_posture.replace('_', ' ')}\n"
                  f"Employees ({len(included)}):\n"
                  + json.dumps([_facts(r, tele) for r in included], ensure_ascii=False, indent=1))
        try:
            invoke(make_agent(NAME, SYSTEM_PROMPT, [send_push, send_email]), prompt, state,
                   timeout=settings.notify_agent_timeout)
        except AgentUnavailable as e:
            record_unavailable(NAME, str(e), "template messages", state)

    # Backfill: anyone the agent didn't reach gets the deterministic template; excluded
    # employees get a skip record. invoke() has already closed the agent's send window.
    sent_by_agent = {n.employee_id for n in db.scalars(
        select(Notification).where(Notification.decision_id == decision_id))}
    missed = [r.name for r in included if r.employee_id not in sent_by_agent]
    if included and missed and len(missed) < len(included):
        record.append_now("agent.send_backfill", {"agent": NAME, "decision_id": decision_id,
                                                  "evaluation_id": rec.evaluation_id, "missed": missed},
                          facility_id=facility.id, evaluation_id=rec.evaluation_id)
    return dispatch(db, decision_id)

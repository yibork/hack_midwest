"""Message agent: writes notification wording. It never decides who is told what.

ONE model call per notice drafts a template for each *group* of recipients who share the same
facts (message type, posture, instruction, language, plain-language need, ack, update). Personal
details are filled in by code through placeholders ({name}, {instruction}, {access}, ...), so the
model never sees names, homes or accommodation needs, and its output stays within max_tokens.
Every rendered message is validated (instruction present verbatim, only computed times, only
numbers from the facts, no emoji); anything that fails gets the deterministic template.

Drafting happens at recommendation time (background) so approval sends instantly; it runs again
only for an override or a new notice version, and only for groups not drafted before.
"""
import json
import logging
import threading

from pydantic import BaseModel
from sqlalchemy import select

from .. import record
from ..config import settings
from ..db import SessionLocal
from ..models import MessageDraft
from ..notify import instructions as ins
from ..notify import messages as msg
from . import runtime
from .runtime import AgentUnavailable, invoke, make_agent, new_state, record_unavailable

log = logging.getLogger(__name__)
NAME = "message_agent"
MAX_GROUPS = 6  # keeps one call's output under max_tokens 1024
MAX_WORDS = 70

SYSTEM_PROMPT = f"""You write short notification templates for staff of a USDA facility about a \
weather operating status that has already been decided. You only choose the wording.

For each group, write a subject (max 10 words) and a body (max {MAX_WORDS} words) in the group's \
language ("en" English, "es" Spanish). Use these placeholders exactly; code fills them in per person:
  {{name}} first name, {{why}} why it applies to them, {{instruction}} what they must do (exact \
wording; never rephrase it), {{access}} their building access, {{facility}}, {{date}},
  {{ack_line}} only if requires_ack is true, {{update_line}} only if has_update_line is true.
The body MUST contain {{name}}, {{instruction}} and {{access}}. Structure: greeting with {{name}}; \
what happened (posture, facility, date); that it applies to them because {{why}}; what to do: \
{{instruction}}; building access: {{access}}; then {{ack_line}} if required.
Rules: no emoji, no times or numbers except those in the facts given, no advice beyond the facts, \
do not name any other posture. If plain_language is true use short sentences and everyday words. \
Return every group's key unchanged."""


class GroupDraft(BaseModel):
    key: str
    subject: str
    body: str


class NoticeDrafts(BaseModel):
    groups: list[GroupDraft]


def _audience(codes: list[str]) -> str:
    for code, text in (("EMERGENCY_DUTY", "emergency-designated staff"), ("FACILITY_OPS", "facility operations staff"),
                       ("CONTRACTOR_STATUS_ONLY", "contractors (building status only)"),
                       ("TELEWORK_SCHEDULED", "staff already teleworking"),
                       ("HOME_IN_WARNING_AREA", "staff whose commute may be affected"),
                       ("ROUTE_CLOSED", "staff whose commute may be affected"), ("ALL_CLEAR", "staff told earlier")):
        if code in codes:
            return text
    return "staff scheduled at the building"


def group_facts(p, ctx) -> dict:
    f = p.facts
    return {
        "key": p.group_key, "language": f.lang, "message_type": f.message_type, "change": f.change,
        "posture": ins.posture_label(ctx.posture, f.lang), "facility": ctx.facility_name, "date": f.date_text,
        **({"delay_hours": f.delay_hours} if ctx.posture == "delayed_arrival" else {}),
        "audience": _audience(p.reason_codes), "plain_language": "plain_language" in f.render,
        "requires_ack": f.requires_ack, "has_update_line": bool(f.update_line),
        "example_instruction": f.instruction,
    }


def _required_placeholders(g: dict) -> list[str]:
    req = ["{name}", "{instruction}", "{access}"]
    if g["requires_ack"]:
        req.append("{ack_line}")
    if g["has_update_line"]:
        req.append("{update_line}")
    return req


def draft_groups(groups: list[dict], state: dict, timeout: float) -> dict[str, GroupDraft]:
    """One model call for all groups. Returns the drafts that have the required placeholders."""
    agent = make_agent(NAME, SYSTEM_PROMPT)
    prompt = "Write a template for each group:\n" + json.dumps(groups, ensure_ascii=False, indent=1)
    result = invoke(agent, prompt, state, timeout, structured_output_model=NoticeDrafts)
    out = result.structured_output
    if not isinstance(out, NoticeDrafts):
        raise AgentUnavailable("message agent returned no structured output")
    wanted = {g["key"]: g for g in groups}
    good = {}
    for d in out.groups:
        g = wanted.get(d.key)
        if g and all(ph in d.body for ph in _required_placeholders(g)) and len(d.body.split()) <= MAX_WORDS + 20:
            good[d.key] = d
    return good


def fill_messages(plan, *, allow_llm: bool, evaluation_id: str | None, facility_id: str, budget_key: str,
                  wait_for_predraft: str | None = None, store_only: bool = False) -> dict:
    """Set subject/message/drafted_by on every included personal message of the plan."""
    if wait_for_predraft:
        wait_predraft(wait_for_predraft, timeout=settings.agent_timeout)
    personal = [p for p in plan.planned if p.included and p.facts is not None]
    ids = {"evaluation_id": evaluation_id, "facility_id": facility_id}
    if not any(p.message_type in ("action_required", "fyi", "call_in") for p in personal):
        # Nothing to word for anyone (e.g. normal operations, no exposure): no model call at all.
        record.append_now("messages.skipped", {"agent": NAME, **ids, "posture": plan.ctx.posture,
                                               "reason": "no action_required or fyi recipients"},
                          facility_id=facility_id, evaluation_id=evaluation_id)
        return {"groups": 0, "from_cache": 0, "drafted": 0, "agent_messages": 0, "template_messages": 0,
                "rejected": [], "skipped": True}
    size: dict[str, int] = {}
    first: dict[str, object] = {}
    for p in personal:
        size[p.group_key] = size.get(p.group_key, 0) + 1
        first.setdefault(p.group_key, p)
    with SessionLocal() as s:
        drafts = {d.key: (d.subject, d.body) for d in s.scalars(select(MessageDraft).where(MessageDraft.key.in_(list(size))))}
    cached = len(drafts)
    missing = sorted((k for k in size if k not in drafts), key=lambda k: -size[k])
    stats = {"groups": len(size), "from_cache": cached, "drafted": 0, "agent_messages": 0, "template_messages": 0,
             "rejected": []}
    if missing and allow_llm and runtime.llm_configured():
        state = new_state(**ids, llm_budget_key=budget_key)
        try:
            new = draft_groups([group_facts(first[k], plan.ctx) for k in missing[:MAX_GROUPS]], state,
                               settings.agent_timeout)
            with SessionLocal() as s:
                for k, d in new.items():
                    if s.get(MessageDraft, k) is None:
                        s.add(MessageDraft(key=k, subject=d.subject.strip(), body=d.body.strip(), source="agent"))
                s.commit()
            drafts.update({k: (d.subject.strip(), d.body.strip()) for k, d in new.items()})
            stats["drafted"] = len(new)
        except AgentUnavailable as e:
            record_unavailable(NAME, str(e), "template messages", state)
    if store_only:
        return stats

    for p in personal:
        subject, body = msg.template(p.facts)
        p.drafted_by = "template"
        if p.group_key in drafts:
            try:
                d_subject, d_body = msg.render_draft(*drafts[p.group_key], p.facts)
                reason = msg.validate(d_body, p.facts)
            except (KeyError, ValueError, IndexError) as e:
                reason = f"bad template: {e}"
            if reason is None:
                subject, body, p.drafted_by = d_subject, d_body, "agent"
            elif len(stats["rejected"]) < 10:
                stats["rejected"].append({"group": p.group_key[:8], "reason": reason})
        p.subject, p.message = subject, body
        stats["agent_messages" if p.drafted_by == "agent" else "template_messages"] += 1
    record.append_now("messages.drafted", {"agent": NAME, **ids, **stats}, facility_id=facility_id,
                      evaluation_id=evaluation_id)
    return stats


# ---------------------------------------------------------------- recommendation-time drafting

_threads: dict[str, threading.Thread] = {}


def _predraft(rec_id: str) -> None:
    from ..models import Facility, Recommendation
    from ..notify.notices import plan_for

    try:
        with SessionLocal() as db:
            rec = db.get(Recommendation, rec_id)
            facility = db.get(Facility, rec.facility_id)
            plan = plan_for(db, facility, rec, rec.posture)
        fill_messages(plan, allow_llm=True, evaluation_id=rec.evaluation_id, facility_id=facility.id,
                      budget_key=f"predraft:{rec_id}", store_only=True)
    except Exception:  # noqa: BLE001 - background best effort; templates cover any failure
        log.exception("predraft failed for %s", rec_id)


def start_predraft(rec_id: str) -> None:
    if not (settings.predraft and runtime.llm_configured()):
        return
    t = threading.Thread(target=_predraft, args=(rec_id,), name=f"predraft-{rec_id[:8]}", daemon=True)
    _threads[rec_id] = t
    t.start()


def wait_predraft(rec_id: str, timeout: float) -> None:
    t = _threads.get(rec_id)
    if t is not None:
        t.join(timeout)

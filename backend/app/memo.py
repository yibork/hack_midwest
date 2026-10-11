"""Printable, privacy-safe decision record; GET never sends notices or runs agents."""
import html
import re
from collections import Counter
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, record
from .db import get_db, utcnow
from .roles import manager_only
from .services import change_rule_id

router = APIRouter(dependencies=[Depends(manager_only)])
POSTURES = {"normal": "Normal operations", "delayed_arrival": "Delayed arrival", "early_dismissal": "Early dismissal",
            "telework": "Telework", "closure": "Closure"}


def public_text(value):
    """Free text may mention private needs; omit that field rather than disclose it.
    No accommodation, home-location, message or per-person instruction fields are read."""
    text = str(value if value is not None else "Not recorded")
    private_check = re.sub(r"accommodation(?:_| )on(?:_| )file", "", text, flags=re.I)
    if re.search(r"accommodation|mobility|wheelchair|screen.?reader|large.?text|early.?notice|text.?only|plain.?language|medical|disabilit|diagnos", private_check, re.I):
        return "Private employee details omitted from this memo."
    return text


def esc(value):
    return html.escape(public_text(value), quote=True)


def link(url, label):
    if not url or not (url.startswith("/") and not url.startswith("//") or urlparse(url).scheme in {"http", "https"}):
        return esc(label)
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def table(headings, rows):
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return "<div class='table-scroll'><table><thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headings) + "</tr></thead><tbody>" + body + "</tbody></table></div>"


def render(db, decision):
    rec = db.get(models.Recommendation, decision.recommendation_id)
    facility = db.get(models.Facility, rec.facility_id)
    zone = ZoneInfo(facility.timezone)

    def when(value):
        return esc(value.astimezone(zone).strftime("%a %b %d, %H:%M %Z") if value else None)

    events = db.scalars(select(models.RecordEvent).where(models.RecordEvent.facility_id == facility.id).order_by(models.RecordEvent.id)).all()
    anchor = next((e for e in events if e.id == decision.record_event_id), None) if decision.record_event_id else next(
        (e for e in events if e.event_type == "decision.recorded" and e.payload.get("decision_id") == decision.id), None)
    status = record.verify(db, facility.id)
    at_decision = record.verify(db, facility.id, anchor.id) if anchor else None
    badge = "Record verified" if status["ok"] else f"Record broken at event {status['first_broken_event_id']}: {status['reason']}"
    evidence = db.scalars(select(models.Evidence).where(models.Evidence.id.in_(rec.evidence_ids)).order_by(models.Evidence.retrieved_at)).all()
    def simulated(e):
        return e.scenario_kind == 'simulated' or e.source == 'demo' or isinstance(e.value, dict) and bool(e.value.get('simulated'))
    mode = "SIMULATED" if rec.scenario_kind == "simulated" else "REPLAY" if rec.scenario_kind == "replay" else "LIVE evidence"
    if mode == "LIVE evidence" and any(simulated(e) for e in evidence):
        mode = "Contains SIMULATED evidence (historical demo)"
    sections = [f"<p class='demo'>DEMO / synthetic facility and roster · {esc(mode)} · Not an official government system</p>",
                f"<h1>Decision memo</h1><h2>{esc(facility.name)}</h2>",
                f"<p>{esc(facility.address)}</p><p><strong>{esc(badge)}</strong></p>",
                "<h2>Decision and target day</h2>",
                table(["Field", "Recorded value"], [
                    ["Target day", esc(rec.operating_date.strftime('%a %b %d, %Y'))], ["Why this target day", esc(rec.target_note)],
                    ["Decision time", when(decision.decided_at)], ["Manager", esc(decision.manager_name)],
                    ["Recommended posture", esc(POSTURES.get(rec.posture, rec.posture))],
                    ["Final posture", esc(POSTURES.get(decision.final_posture, decision.final_posture))],
                    ["Action", esc(decision.action.capitalize())], ["Override reason", esc(decision.reason or "No override")],
                    ["Delay hours", esc(decision.delay_hours)], ["Dismissal time", esc(decision.dismissal_time)],
                    ["Policy version", esc(rec.policy_version)],
                ])]
    trigger_rows = []
    for t in rec.triggers:
        citation = t.get("citation") or "Citation not saved in this historical trigger"
        if isinstance(citation, dict):
            citation_html = link(citation.get("url"), citation.get("title", "Source")) + "<br>" + esc(citation.get("publisher")) + " · " + esc(citation.get("section_or_page")) + "<br>Verified " + esc(citation.get("retrieved_at"))
        else:
            citation_html = esc(citation)
        unit = " " + esc(t["units"]) if t.get("units") else ""
        trigger_rows.append([link("/thresholds#" + t["rule_id"], t.get("description") or t["rule_id"].replace("_", " ")),
                             esc(t.get("threshold")) + unit, esc(t.get("observed")) + unit, esc(t.get("margin")) + unit,
                             citation_html, esc(rec.policy_version)])
    sections += ["<h2>Triggers and threshold citations</h2>", table(["Rule", "Threshold", "Observed", "Margin", "Citation", "Policy"], trigger_rows) if trigger_rows else "<p>No threshold fired.</p>"]
    lead = anchor.payload.get("lead_time_hours") if anchor else None
    sections += ["<h2>Confidence and forecast lead time</h2>", f"<p><strong>{esc(rec.confidence.capitalize())}</strong> · At evaluation: {esc(rec.lead_time_hours)} hours · At decision: {esc(lead)} hours</p>",
                 "<ul>" + "".join(f"<li>{esc(r)}</li>" for r in rec.confidence_reasons) + "</ul>",
                 "<h2>What would have changed it</h2>", "<ul>" + "".join(f"<li>{link('/thresholds#' + change_rule_id(r), r)}</li>" for r in rec.what_would_change) + "</ul>" if rec.what_would_change else "<p>No alternative threshold recorded.</p>"]
    evidence_rows = []
    for e in evidence:
        value = e.value if isinstance(e.value, dict) else {}
        authority = value.get("authority") or {"nws": "official federal", "nwps": "official federal", "human": "human reported", "demo": "SIMULATED facility policy", "roster": "facility roster"}.get(e.source, "Not recorded")
        label = "REPLAY · " if e.scenario_kind == "replay" else "SIMULATED · " if simulated(e) else ""
        digest = e.saved_copy_hash or e.snapshot_hash
        saved = link(f"/api/snapshots/{digest}", "Saved copy" + (" (normalized evidence)" if e.saved_copy_kind == "normalized" else "")) if digest else "Saved response not available"
        evidence_rows.append([f'<span id="evidence-{esc(e.id)}">{esc(label + e.source + ': ' + e.kind.replace('_', ' '))}</span><br>' + link(e.url, "Source (live — may have changed)") if e.url else esc(label + e.source + ': ' + e.kind.replace('_', ' ')) + "<br>No live source",
                              when(e.source_time), when(e.retrieved_at), esc(authority), esc(e.status), saved,
                              f"<code>{esc(digest)}</code>"])
    sections += ["<h2>Evidence and saved copies</h2>", table(["Source", "Source time", "Retrieved", "Authority", "Status", "Saved copy", "SHA-256"], evidence_rows)]
    ancestry, cursor = {rec.id}, rec
    while cursor.previous_recommendation_id and cursor.previous_recommendation_id not in ancestry:
        ancestry.add(cursor.previous_recommendation_id)
        cursor = db.get(models.Recommendation, cursor.previous_recommendation_id)
        if cursor is None:
            break
    investigation_rows = []
    for inv in db.scalars(select(models.Investigation).where(models.Investigation.recommendation_id.in_(ancestry), models.Investigation.created_at <= decision.decided_at).order_by(models.Investigation.created_at)):
        answers = [e for e in events if e.event_type == "investigation.answer" and (e.evaluation_id == inv.id or e.payload.get("investigation_id") == inv.id)
                   and e.timestamp <= decision.decided_at and (not anchor or e.id < anchor.id)]
        result = answers[-1].payload if answers else {}
        answer = esc(result.get("answer") or "No completed answer recorded at decision time.")
        # Known evidence citations become local links; unknown IDs remain harmless text.
        for e in evidence:
            answer = answer.replace(esc(f"[evidence:{e.id}]"), link(f"#evidence-{e.id}", "[Saved evidence]"))
        answer = re.sub(r"\[policy:([a-z_]+)\]", lambda match: link('/thresholds#' + match[1], '[' + match[1].replace('_', ' ').capitalize() + ']'), answer)
        investigation_rows.append([when(inv.created_at), esc(inv.question), answer, esc(result.get("source_label") or "Pending at decision time")])
    sections += ["<h2>Investigations and answers</h2>", table(["Requested", "Question", "Answer", "Source label"], investigation_rows) if investigation_rows else "<p>No investigations recorded before this decision.</p>"]
    notice_ids = db.scalars(select(models.Notice.id).where(models.Notice.decision_id == decision.id)).all()
    notes = db.scalars(select(models.Notification).where(models.Notification.notice_id.in_(notice_ids))).all()
    included = [n for n in notes if n.included]
    reasons = Counter(code for n in included for code in n.reason_codes)
    excluded_reasons = Counter(code for n in notes if not n.included for code in n.reason_codes)
    waves = Counter(n.wave for n in included)
    channels = Counter(c for n in included for c in n.channels_planned)
    delivery = Counter("Delivered" if result.get("ok") else "Failed" for n in included for result in n.channel_results.values() if isinstance(result, dict))
    counts = {"Recipients included": len({n.employee_id for n in included}), "Messages included": len(included), "Messages sent": sum(n.sent_at is not None for n in included),
              "Acknowledgments required": sum(n.requires_ack for n in included), "Acknowledgments received": sum(n.acknowledged_at is not None for n in included),
              "Reminders sent": sum(n.reminder_sent_at is not None for n in included), "Escalated messages": sum(n.escalation_level > 0 for n in included),
              "Escalation level 1": sum(n.escalation_level == 1 for n in included), "Escalation level 2": sum(n.escalation_level >= 2 for n in included),
              "Call-ins": sum(n.message_type == "call_in" for n in included)}
    counts.update({f"Delivery: {k}": v for k, v in delivery.items()})
    counts.update({f"Reason code: {k.replace('_', ' ')}": v for k, v in reasons.items()})
    counts.update({f"Not notified, reason code: {k.replace('_', ' ')}": v for k, v in excluded_reasons.items()})
    counts.update({f"Wave: {k}": v for k, v in waves.items()})
    counts.update({f"Channel: {k}": v for k, v in channels.items()})
    sections += ["<h2>Notification summary</h2>", f"<p>Delivery and acknowledgment status as of {when(utcnow())}; later updates do not alter the decision-time chain anchor. Reason-code counts can overlap. No private employee accommodation details or individual messages are included.</p>",
                 table(["Metric", "Count"], [[esc(k), esc(v)] for k, v in counts.items()]),
                 "<h2>Record verification</h2>", f"<p>{esc(badge)} · {status['event_count']} events checked.</p>",
                 f"<p>Chain through the decision: {'verified' if at_decision and at_decision['ok'] else 'not verified'}. Decision event: {esc(anchor.id if anchor else None)}.</p>",
                 f"<p>Chain hash at decision time: <code>{esc(decision.record_hash or (anchor.hash if anchor else None))}</code></p>",
                 f"<details><summary>Technical details</summary><p>Decision ID: {esc(decision.id)}<br>Current head: <code>{esc(status['head_hash'])}</code></p></details>",
                 "<p>Historical events were sealed when this feature was deployed. This is tamper-evident, not independently timestamped or tamper-proof; retain a printed or PDF copy as an external anchor.</p>"]
    style = """body{font:16px 'Public Sans',Arial,sans-serif;color:#1b1b1b;background:#f0f0f0;margin:0;padding:24px}main{max-width:1100px;margin:auto;background:white;padding:32px;border:1px solid #dfe1e2}h1{font-size:28px}h2{font-size:20px;margin-top:28px}a{color:#005ea2}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #dfe1e2}th{background:#e6e6e6}tr:nth-child(even){background:#f9f9f9}code{overflow-wrap:anywhere;font-size:12px}.demo{background:#faf3d1;padding:12px}button{background:#005ea2;color:white;border:0;padding:12px;font:inherit;cursor:pointer}.table-scroll{overflow:auto}@media print{body{background:white;padding:0;font-size:11pt}main{max-width:none;border:0;padding:0}.no-print{display:none}h2{break-after:avoid}tr{break-inside:avoid}thead{display:table-header-group}table{font-size:9pt}.table-scroll{overflow:visible}a{color:inherit}details{display:block}@page{margin:16mm}}"""
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Decision memo — Closure Call</title><style>' + style + '</style></head><body><main><p class="no-print"><button onclick="window.print()">Print / save PDF</button> · <a href="/record">Decision record</a></p>' + "".join(sections) + "</main></body></html>"


@router.get("/decisions/{decision_id}/memo", response_class=HTMLResponse)
@router.get("/api/decisions/{decision_id}/memo", response_class=HTMLResponse)
def decision_memo(decision_id: str, db: Session = Depends(get_db)):
    decision = db.get(models.Decision, decision_id)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    return HTMLResponse(render(db, decision), headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

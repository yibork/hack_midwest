"""Writer agent: rephrases the engine's Recommendation as a paragraph a manager can paste into
an email. It may only rephrase. Any draft that names a different posture or cites a number that
isn't in the facts is discarded in favor of the deterministic template."""
import json
import re
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .. import record
from ..schemas import Evidence, Posture, Recommendation
from .runtime import AgentUnavailable, invoke, make_agent, record_unavailable

NAME = "writer_agent"
MAX_WORDS = 120

# How each posture is spotted in free text (English and Spanish).
POSTURE_PATTERNS = {
    "normal": r"\bnormal (?:operations|posture|schedule)\b|\boperating normally\b|\bopera(?:ción)? con normalidad\b",
    "delayed_arrival": r"\bdelayed[\s_-]+arrival\b|\bdelay(?:ed|ing)? (?:the )?(?:arrival|opening|start)\b|\bllegada retrasada\b",
    "early_dismissal": r"\bearly[\s_-]+dismissal\b|\bdismiss(?:ed|ing)? early\b|\bsalida anticipada\b",
    "telework": r"\btelework\w*|\bteletrabajo\b",
    "closure": r"\bclosure\b|\bclosed\b|\bcerrad[ao]\b|\bcierre\b",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = f"""You write the rationale for a facility operating-posture recommendation made by \
a deterministic rules engine. A USDA manager will paste your paragraph into an email.

Rules:
- One plain-language paragraph, at most {MAX_WORDS} words. No headings, lists or markdown.
- Set stated_posture to the recommended posture exactly as given (e.g. "normal", "delayed_arrival").
- State the recommended posture using the posture label given (e.g. "recommends normal \
operations", "recommends a delayed arrival"). Other postures may appear only in conditional \
sentences from what_would_change (e.g. "gusts of 58 mph would move this to closure") or negations \
("no closure is warranted").
- Mention the target day and why it was chosen if target_note is given.
- Cite sources and times (e.g. "NWS alerts retrieved 11:13 CDT").
- Use only numbers that appear in the facts. Do not round, convert or compute new numbers.
- Say plainly if any data was unavailable, and that the manager must approve before staff are notified.
- Do not add advice or facts that are not in the input."""


class Rationale(BaseModel):
    stated_posture: Posture  # the posture the paragraph recommends; must equal the engine's
    paragraph: str


def build_facts(rec: Recommendation, evidence: list[Evidence], facility_name: str, tz: str) -> dict:
    local = ZoneInfo(tz)

    def t(d):
        return d.astimezone(local).strftime("%H:%M %Z") if d else None

    ev_facts = []
    for e in evidence:
        item = {"source": e.source.upper(), "kind": e.kind, "status": e.status,
                "source_time": t(e.source_time), "retrieved": t(e.retrieved_at), "note": e.relevance_note}
        if e.kind == "active_alerts" and isinstance(e.value, dict):
            item["alerts"] = [{k: a.get(k) for k in ("event", "severity", "certainty", "areaDesc")}
                              for a in e.value.get("alerts", [])]
        if e.kind == "wind_forecast" and isinstance(e.value, dict):
            item["peak_gust_next_24h_mph"] = e.value.get("peak_gust_mph")
        ev_facts.append(item)
    return {
        "facility": facility_name,
        "operating_date": rec.operating_date.strftime("%A, %B %d, %Y"),
        "recommended_posture_label": rec.posture.replace("_", " "),
        "confidence": rec.confidence,
        "confidence_reasons": rec.confidence_reasons,
        "triggers": [t_.model_dump(include={"rule_id", "description", "threshold", "observed", "margin", "units"})
                     for t_ in rec.triggers],
        "what_would_change": rec.what_would_change,
        "evidence": ev_facts,
        "deterministic_summary": rec.rationale,
        "target_note": rec.target_note,
    }


def mentioned_postures(text: str) -> set[str]:
    return {p for p, pat in POSTURE_PATTERNS.items() if re.search(pat, text, re.IGNORECASE)}


_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Sentences where another posture may appear: conditions, what-would-change, negations.
_CONDITIONAL = re.compile(r"\b(would|could|might|if|unless|only if|should .* reach|no .* (is|are) (warranted|needed|"
                          r"required|recommended)|not (warranted|needed|required|recommended)|rather than|instead of|"
                          r"what would change|would move|would change|move this to|upgrade|escalat)", re.I)


def assertive_postures(text: str) -> set[str]:
    """Postures named in sentences that state a recommendation (not conditional or negated)."""
    named: set[str] = set()
    for sentence in _SENTENCE.split(text):
        if not _CONDITIONAL.search(sentence):
            named |= mentioned_postures(sentence)
    return named


def validate_rationale(text: str, posture: str, facts: dict, stated_posture: str | None = None) -> str | None:
    """Return a rejection reason, or None if the draft is acceptable.

    - stated_posture (structured output) must equal the recommendation.
    - The paragraph must state the recommended posture, and may name other postures only in
      conditional / what-would-change / negated sentences ("no closure is warranted", "gusts of
      58 mph would move this to closure").
    - Every number must appear in the facts. At most MAX_WORDS words."""
    if not text or not text.strip():
        return "empty draft"
    if stated_posture is not None and stated_posture != posture:
        return f"stated_posture {stated_posture} but the recommendation is {posture}"
    words = len(text.split())
    if words > MAX_WORDS:
        return f"too long ({words} words > {MAX_WORDS})"
    wrong = assertive_postures(text) - {posture}
    if wrong:
        return f"names posture(s) {sorted(wrong)} but the recommendation is {posture}"
    label = posture.replace("_", " ")
    if posture not in mentioned_postures(text) and not re.search(rf"\b{label}\b", text, re.IGNORECASE):
        return f"does not state the recommended posture ({label})"
    allowed = {float(n) for n in _NUMBER.findall(json.dumps(facts, default=str))}
    unknown = sorted({n for n in _NUMBER.findall(text) if float(n) not in allowed}, key=float)
    if unknown:
        return f"mentions number(s) not in the triggers/evidence: {', '.join(unknown)}"
    return None


def draft(rec: Recommendation, facts: dict, state: dict, timeout: float) -> tuple[str, str]:
    agent = make_agent(NAME, SYSTEM_PROMPT)
    prompt = "Write the rationale paragraph from these facts:\n" + json.dumps(facts, default=str, indent=1)
    result = invoke(agent, prompt, state, timeout, structured_output_model=Rationale)
    out = result.structured_output
    if not isinstance(out, Rationale):
        raise AgentUnavailable("writer returned no structured output")
    return " ".join(out.paragraph.split()), out.stated_posture


def write_rationale(rec: Recommendation, evidence: list[Evidence], *, facility_name: str, tz: str,
                    state: dict, timeout: float) -> tuple[str, str]:
    """-> (rationale, "agent" | "template"). rec.rationale holds the engine's template."""
    facts = build_facts(rec, evidence, facility_name, tz)
    try:
        text, stated = draft(rec, facts, state, timeout)
    except AgentUnavailable as e:
        record_unavailable(NAME, str(e), "template rationale", state)
        return rec.rationale, "template"
    reason = validate_rationale(text, rec.posture, facts, stated)
    ids = {"evaluation_id": state.get("evaluation_id"), "facility_id": state.get("facility_id")}
    if reason:
        record.append_now("writer_rejected", {"agent": NAME, **ids, "reason": reason, "draft": text},
                          facility_id=ids["facility_id"], evaluation_id=ids["evaluation_id"])
        return rec.rationale, "template"
    record.append_now("writer_accepted", {"agent": NAME, **ids, "words": len(text.split())},
                      facility_id=ids["facility_id"], evaluation_id=ids["evaluation_id"])
    return text, "agent"

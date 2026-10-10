"""Pydantic models: the data contract shared by the API, rules engine and frontend (src/api.ts)."""
import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Posture = Literal["normal", "delayed_arrival", "early_dismissal", "telework", "closure"]
# Least to most restrictive. The engine picks the highest-ranked posture among fired rules.
POSTURE_ORDER: list[str] = ["normal", "delayed_arrival", "early_dismissal", "telework", "closure"]
EvidenceSource = Literal["nws", "nwps", "modot", "roster"]
EvidenceStatus = Literal["ok", "unknown", "stale"]
Confidence = Literal["high", "medium", "low"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Facility(ORMModel):
    id: str
    name: str
    address: str = ""
    lat: float
    lon: float
    timezone: str = "America/Chicago"
    open_time: str  # "HH:MM" local
    close_time: str
    access_routes: list[str] = []
    temporary: bool = False


class Employee(ORMModel):
    id: str
    name: str
    assigned_facility_id: str
    temp_assignment_facility_id: str | None = None
    temp_assignment_until: dt.date | None = None
    essential: bool = False
    telework_eligible: bool = True
    accessibility_needs: str | None = None
    preferred_channel: Literal["push", "email"]
    contact: str
    language: Literal["en", "es"] = "en"
    typical_departure_time: str


class DailySchedule(ORMModel):
    employee_id: str
    date: dt.date
    status: Literal["onsite", "telework", "leave"]


class Evidence(ORMModel):
    id: str
    facility_id: str | None = None
    evaluation_id: str | None = None
    source: EvidenceSource
    kind: str
    value: Any = None
    units: str | None = None
    source_time: dt.datetime | None = None
    retrieved_at: dt.datetime
    valid_until: dt.datetime | None = None
    url: str | None = None
    snapshot_hash: str | None = None
    relevance_note: str = ""
    ambiguity_flags: list[str] = []
    status: EvidenceStatus


class Trigger(BaseModel):
    rule_id: str
    description: str
    threshold: float | str | None = None
    observed: float | str | None = None
    margin: float | None = None  # observed - threshold; positive means exceeded
    units: str | None = None
    citation: str
    posture: Posture
    evidence_id: str | None = None


class Recommendation(ORMModel):
    id: str
    facility_id: str
    evaluation_id: str | None = None
    evaluated_at: dt.datetime
    operating_date: dt.date
    posture: Posture
    triggers: list[Trigger]
    confidence: Confidence
    confidence_reasons: list[str]
    what_would_change: list[str]
    rationale: str
    rationale_source: Literal["agent", "template"] = "template"
    evidence_ids: list[str]
    policy_version: str


class DecisionIn(BaseModel):
    manager_name: str
    action: Literal["approve", "override"]
    final_posture: Posture | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _override_needs_reason(self):
        if not self.manager_name.strip():
            raise ValueError("manager_name is required")
        if self.action == "override":
            if not (self.reason and self.reason.strip()):
                raise ValueError("reason is required when overriding")
            if self.final_posture is None:
                raise ValueError("final_posture is required when overriding")
        return self


class Decision(ORMModel):
    id: str
    recommendation_id: str
    manager_name: str
    action: Literal["approve", "override"]
    final_posture: Posture
    reason: str | None = None
    decided_at: dt.datetime


class RecommendationDetail(Recommendation):
    facility: Facility
    evidence: list[Evidence]
    decision: Decision | None = None
    # Earliest typical departure of included employees minus 30 minutes.
    decide_by: dt.datetime | None = None


class Recipient(BaseModel):
    employee_id: str
    name: str
    included: bool
    reason: str
    channel: Literal["push", "email"]
    contact: str
    language: Literal["en", "es"]
    essential: bool
    early_notice: bool
    typical_departure_time: str
    message: str | None = None


class Notification(ORMModel):
    id: str
    decision_id: str
    employee_id: str
    included: bool
    reason: str
    channel: str
    message: str | None = None
    sent_at: dt.datetime | None = None
    delivered: bool
    acknowledged_at: dt.datetime | None = None


class RecordEvent(ORMModel):
    id: int
    timestamp: dt.datetime
    facility_id: str | None = None
    evaluation_id: str | None = None
    event_type: str
    payload: dict


class NationalAlert(BaseModel):
    id: str
    event: str
    severity: str
    certainty: str
    urgency: str
    onset: str | None = None
    ends: str | None = None
    headline: str | None = None
    area_desc: str | None = None
    sent: str | None = None
    lat: float | None = None
    lon: float | None = None


# Optional client-chosen id so the UI can poll /api/agent-activity while the evaluation runs.
EvaluationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{8,64}$")]


class EvaluateIn(BaseModel):
    evaluation_id: EvaluationId | None = None


class EvaluatePointIn(EvaluateIn):
    lat: float
    lon: float
    name: str


class RecordAt(BaseModel):
    as_of: dt.datetime
    facility_id: str
    recommendation: RecommendationDetail | None = None
    notifications: list[Notification] = []

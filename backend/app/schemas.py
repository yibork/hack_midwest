"""Pydantic models: the data contract shared by the API, rules engine and frontend (src/api.ts)."""
import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Posture = Literal["normal", "delayed_arrival", "early_dismissal", "telework", "closure"]
# Least to most restrictive. The engine picks the highest-ranked posture among fired rules.
POSTURE_ORDER: list[str] = ["normal", "delayed_arrival", "early_dismissal", "telework", "closure"]
EvidenceSource = Literal["nws", "nwps", "modot", "roster", "demo"]
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


Role = Literal["employee", "supervisor", "facility_manager", "facility_ops", "security_desk", "contractor"]
Designation = Literal["emergency", "telework_ready", "non_telework"]
AccommodationNeed = Literal["early_notice", "mobility_early_dismissal", "text_only", "screen_reader",
                            "large_text", "plain_language"]
AccessLevel = Literal["standard", "emergency", "facility_ops", "contractor_badge", "escort_required"]
Channel = Literal["push", "email"]
ShiftStatus = Literal["onsite", "telework", "leave", "day_off"]
MessageType = Literal["action_required", "fyi", "supervisor_summary", "access_list", "staffing_alert",
                      "call_in", "none"]


class EmployeePublic(ORMModel):
    """What anyone but the person themself may see. No accommodation details, no home location."""
    id: str
    name: str
    role: Role = "employee"
    designation: Designation
    supervisor_id: str | None = None
    functions: list[str] = []
    primary_function: str | None = None
    accommodation_on_file: bool = False
    language: Literal["en", "es"] = "en"
    preferred_channels: list[Channel] = ["email"]
    commute_minutes: int = 30
    access_level: AccessLevel = "standard"


class Employee(EmployeePublic):
    """Internal: input to the impact engine. Never returned by the API."""
    accommodation_needs: list[AccommodationNeed] = []
    ntfy_topic: str | None = None
    email: str = ""
    home_lat: float = 0.0
    home_lon: float = 0.0
    home_zone: str | None = None
    home_county: str | None = None
    commute_routes: list[str] = []


class Shift(ORMModel):
    employee_id: str
    date: dt.date
    status: ShiftStatus
    shift_start: str | None = None  # "HH:MM" local; shift_end < shift_start means it ends next day
    shift_end: str | None = None


class Assignment(ORMModel):
    employee_id: str
    facility_id: str
    kind: Literal["permanent", "detail_in", "detail_out"]
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class CriticalFunction(ORMModel):
    facility_id: str
    code: str
    name: str
    min_onsite: dict[str, int] = {}
    notes: str = ""


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


HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class DecisionIn(BaseModel):
    manager_name: str
    action: Literal["approve", "override"]
    final_posture: Posture | None = None
    reason: str | None = None
    # Posture parameters; defaults from notify/instructions.yaml.
    delay_hours: int | None = Field(default=None, ge=1, le=6)
    dismissal_time: HHMM | None = None

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
    delay_hours: int | None = None
    dismissal_time: str | None = None


class RecommendationDetail(Recommendation):
    facility: Facility
    evidence: list[Evidence]
    decision: Decision | None = None
    # Decision clock: earliest notify_by among action-required recipients (see people.clock).
    decide_by: dt.datetime | None = None
    people: "PeopleSummary | None" = None


class Notice(ORMModel):
    id: str
    decision_id: str
    facility_id: str
    operating_date: dt.date
    version: int
    supersedes_notice_id: str | None = None
    kind: Literal["initial", "update", "all_clear"]
    posture: Posture
    params: dict = {}
    created_at: dt.datetime
    sent_at: dt.datetime | None = None


class ChannelResult(BaseModel):
    sent_at: dt.datetime | None = None
    ok: bool
    error: str | None = None
    fallback: bool = False  # email sent because push failed


class Notification(ORMModel):
    """Manager view. Reason codes are public codes (accommodations appear only as ACCOMMODATION_ON_FILE)."""
    id: str
    notice_id: str
    employee_id: str
    included: bool
    reason_codes: list[str]
    reason_text: str
    audience_text: str
    duty_instruction: str
    building_access: str
    access_code: str = ""
    notify_by: dt.datetime | None = None
    wave: Literal["1", "2", "fyi"]
    message_type: MessageType
    change: Literal["initial", "update", "all_clear", "unchanged"]
    requires_ack: bool
    function_code: str | None = None
    channels_planned: list[str]
    channel_results: dict[str, ChannelResult] = {}
    subject: str | None = None
    message: str | None = None
    drafted_by: str | None = None
    sent_at: dt.datetime | None = None
    acknowledged_at: dt.datetime | None = None
    reminder_sent_at: dt.datetime | None = None
    escalation_level: int = 0
    escalated_to: str | None = None


class TeamNotification(BaseModel):
    """Supervisor view: no reason details beyond audience_text, no message bodies."""
    notification_id: str
    employee_id: str
    name: str
    message_type: MessageType
    included: bool
    audience_text: str
    duty_instruction: str
    building_access: str
    wave: str
    notify_by: dt.datetime | None = None
    channels_planned: list[str]
    delivered: bool | None = None  # None until sent
    sent_at: dt.datetime | None = None
    requires_ack: bool
    acknowledged_at: dt.datetime | None = None
    escalation_level: int = 0


class ExposureSuggestion(BaseModel):
    employee_id: str
    name: str
    audience_text: str
    suggestion: str


class TeamBoard(BaseModel):
    supervisor: EmployeePublic
    notice: Notice | None = None
    members: list[EmployeePublic]
    notifications: list[TeamNotification]
    suggestions: list[ExposureSuggestion]


class AccessRow(BaseModel):
    employee_id: str
    name: str
    role: Role
    access_level: AccessLevel
    access: str  # "allowed" | "denied" | "allowed_after 09:30" | "allowed_until 14:00" | "allowed_with_escort"
    window: str  # human-readable time window
    code: str
    reason: str
    scheduled: str  # onsite | telework | leave | day_off


class AccessList(BaseModel):
    facility_id: str
    facility_name: str
    operating_date: dt.date
    posture: Posture
    notice_id: str | None = None
    policy: str
    rows: list[AccessRow]


class BackupCandidate(BaseModel):
    employee_id: str
    name: str
    designation: Designation
    commute_minutes: int
    scheduled: str


class CalledIn(BaseModel):
    employee_id: str
    name: str
    notification_id: str
    acknowledged: bool


class CoverageItem(BaseModel):
    code: str
    name: str
    required: int
    available: list[str]  # names
    exposed: list[str]  # qualified emergency staff not counted because of commute exposure
    called_in: list[CalledIn] = []
    backups: list[BackupCandidate] = []
    gap: int
    status: Literal["covered", "pending", "shortfall"]
    reason_code: Literal["STAFFING_COVERED", "STAFFING_SHORTFALL"]
    label: str  # "Animal care 1/2 - shortfall"


class DecisionClock(BaseModel):
    at: dt.datetime | None = None
    employee_id: str | None = None
    label: str  # "first: Dana R., early shift 05:00, 40 min commute"


class PeopleSummary(BaseModel):
    posture: Posture
    params: dict
    clock: DecisionClock
    exposed_count: int
    coverage: list[CoverageItem]
    counts_by_code: dict[str, int]
    action_required: int
    fyi: int


class NoticeDetail(BaseModel):
    notice: Notice
    previous: Notice | None = None
    clock: DecisionClock
    coverage: list[CoverageItem]
    notifications: list[Notification]
    names: dict[str, str]  # employee_id -> name


class CallInIn(BaseModel):
    employee_id: str
    function_code: str
    manager_name: str


class MyStatus(BaseModel):
    employee: EmployeePublic
    facility_name: str
    operating_date: dt.date | None = None
    posture: Posture | None = None
    notification: Notification | None = None  # the person's own latest notification
    duty_instruction: str
    building_access: str
    ack_url: str | None = None


class Persona(BaseModel):
    employee_id: str
    name: str
    role: Role
    ntfy_topic: str | None = None
    description: str


class Viewers(BaseModel):
    supervisors: list[EmployeePublic]
    security_desk: list[EmployeePublic]
    employees: list[EmployeePublic]
    personas: list[Persona]


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


RecommendationDetail.model_rebuild()

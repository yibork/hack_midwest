"""SQLAlchemy models. Field names mirror schemas.py (the data contract)."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow


class Facility(Base):
    __tablename__ = "facilities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    open_time: Mapped[str] = mapped_column(String(5))  # "HH:MM" local
    close_time: Mapped[str] = mapped_column(String(5))
    # Access segments: [{route, name, points: [[lat, lon], ...], buffer_km, ramps}] (plain names accepted)
    access_routes: Mapped[list] = mapped_column(JSON, default=list)
    operating_days: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)  # None = Mon-Fri
    # Created by /api/evaluate-point (national scan); hidden from the facility list.
    temporary: Mapped[bool] = mapped_column(Boolean, default=False)


class Employee(Base):
    """A person linked to facilities through Assignments. Accommodation needs are private: only
    the person's own messages use them; everyone else sees accommodation_on_file."""

    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # employee | supervisor | facility_manager | facility_ops | security_desk | contractor
    role: Mapped[str] = mapped_column(String(20), default="employee")
    designation: Mapped[str] = mapped_column(String(20))  # emergency | telework_ready | non_telework
    supervisor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    functions: Mapped[list[str]] = mapped_column(JSON, default=list)  # critical function codes
    primary_function: Mapped[str | None] = mapped_column(String(40), nullable=True)
    accommodation_on_file: Mapped[bool] = mapped_column(Boolean, default=False)
    accommodation_needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(2), default="en")  # en | es
    preferred_channels: Mapped[list[str]] = mapped_column(JSON, default=list)  # ordered: push | email
    ntfy_topic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(200))
    home_lat: Mapped[float] = mapped_column(Float)
    home_lon: Mapped[float] = mapped_column(Float)
    home_zone: Mapped[str | None] = mapped_column(String(10), nullable=True)  # NWS forecast zone, e.g. MOZ037
    home_county: Mapped[str | None] = mapped_column(String(10), nullable=True)  # NWS county, e.g. MOC095
    commute_minutes: Mapped[int] = mapped_column(Integer, default=30)
    commute_routes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # standard | emergency | facility_ops | contractor_badge | escort_required
    access_level: Mapped[str] = mapped_column(String(20), default="standard")


class Shift(Base):
    __tablename__ = "shifts"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(10))  # onsite | telework | leave | day_off
    shift_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM" local
    shift_end: Mapped[str | None] = mapped_column(String(5), nullable=True)  # < start: ends next day


class Assignment(Base):
    """permanent: home facility. detail_in: temporarily working at facility_id (home is elsewhere).
    detail_out: temporarily away from the permanent facility, working at facility_id."""

    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    facility_id: Mapped[str] = mapped_column(String(64), index=True)  # plain id: other offices aren't modeled
    kind: Mapped[str] = mapped_column(String(12))  # permanent | detail_in | detail_out
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class CriticalFunction(Base):
    __tablename__ = "critical_functions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))  # animal_care | lab_freezer_monitoring | security | it_ops
    name: Mapped[str] = mapped_column(String(100))
    min_onsite: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)  # posture -> people
    operating_days: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)  # None = facility days
    notes: Mapped[str] = mapped_column(Text, default="")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    facility_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16))  # nws | nwps | modot | roster | demo
    kind: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSON)
    units: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relevance_note: Mapped[str] = mapped_column(Text, default="")
    # Set by the weather agent when relevance is uncertain (e.g. "alert area adjacent").
    ambiguity_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10))  # ok | unknown | stale


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    posture: Mapped[str] = mapped_column(String(20))
    triggers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    confidence: Mapped[str] = mapped_column(String(10))
    confidence_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    what_would_change: Mapped[list[str]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text)
    rationale_source: Mapped[str] = mapped_column(String(10), default="template")  # agent | template
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(64))
    target_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    lead_time_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)  # engine text, kept when an AI draft replaces it
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # preliminary | final (None = final)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    manager_name: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(10))  # approve | override
    final_posture: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Posture parameters chosen by the manager (defaults in notify/instructions.yaml).
    delay_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dismissal_time: Mapped[str | None] = mapped_column(String(5), nullable=True)


class Notice(Base):
    """One version of what a facility's people were told for an operating date. A changed decision
    creates a new Notice that supersedes the previous one."""

    __tablename__ = "notices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), unique=True)
    facility_id: Mapped[str] = mapped_column(String(64), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_notice_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))  # initial | update | all_clear
    posture: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # delay_hours, report_time, dismissal_time
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("notice_id", "employee_id", "message_type"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notices.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    included: Mapped[bool] = mapped_column(Boolean)  # False: considered, nothing to send
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)  # public codes only
    reason_text: Mapped[str] = mapped_column(Text, default="")  # manager only
    audience_text: Mapped[str] = mapped_column(Text, default="")  # privacy-safe, for supervisors
    duty_instruction: Mapped[str] = mapped_column(Text, default="")
    building_access: Mapped[str] = mapped_column(String(40), default="")
    access_code: Mapped[str] = mapped_column(String(40), default="")
    notify_by: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    wave: Mapped[str] = mapped_column(String(4), default="fyi")  # "1" | "2" | "fyi"
    # action_required | fyi | supervisor_summary | access_list | staffing_alert | call_in | none
    message_type: Mapped[str] = mapped_column(String(20))
    change: Mapped[str] = mapped_column(String(10), default="initial")  # initial | update | all_clear | unchanged
    requires_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    function_code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # call-ins
    channels_planned: Mapped[list[str]] = mapped_column(JSON, default=list)
    channel_results: Mapped[dict] = mapped_column(JSON, default=dict)  # channel -> {sent_at, ok, error}
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    drafted_by: Mapped[str | None] = mapped_column(String(10), nullable=True)  # agent | template
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    escalated_to: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MessageDraft(Base):
    """Message wording for one group of recipients (same facts), drafted once and reused."""

    __tablename__ = "message_drafts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # hash of the group's facts
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10))  # agent
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RoadFactsCache(Base):
    """Validated road facts for a set of road-source contents (+ facility, target day, geometry).
    Unchanged sources -> reuse the facts without a model call."""

    __tablename__ = "road_facts_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    facility_id: Mapped[str] = mapped_column(String(64), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    source_hashes: Mapped[dict] = mapped_column(JSON, default=dict)  # source_id -> content hash
    entries: Mapped[list] = mapped_column(JSON, default=list)  # [{fact, item, source_id, how}]
    rejected: Mapped[list] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(12))  # evaluation | background
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class ScheduledJob(Base):
    """Automatic re-evaluations: 15:00 the day before the target day and 04:00 on it. They run the
    deterministic pipeline and call agents only if the result changed."""

    __tablename__ = "scheduled_jobs"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)  # facility|date|kind
    facility_id: Mapped[str] = mapped_column(String(64), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))  # day_before_1500 | target_0400
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done | skipped | failed
    ran_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class RecordEvent(Base):
    """Append-only. The listeners below refuse UPDATE and DELETE through the ORM."""

    __tablename__ = "record_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    facility_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


@event.listens_for(RecordEvent, "before_update")
def _refuse_update(mapper, connection, target):
    raise RuntimeError("RecordEvent is append-only; updates are not allowed")


@event.listens_for(RecordEvent, "before_delete")
def _refuse_delete(mapper, connection, target):
    raise RuntimeError("RecordEvent is append-only; deletes are not allowed")

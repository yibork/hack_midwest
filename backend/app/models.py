"""SQLAlchemy models. Field names mirror schemas.py (the data contract)."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text, event
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
    access_routes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Created by /api/evaluate-point (national scan); hidden from the facility list.
    temporary: Mapped[bool] = mapped_column(Boolean, default=False)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Plain ids, no FK: the "elsewhere" offices are not modeled as facilities yet.
    assigned_facility_id: Mapped[str] = mapped_column(String(64), index=True)
    temp_assignment_facility_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    temp_assignment_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    essential: Mapped[bool] = mapped_column(Boolean, default=False)
    telework_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    accessibility_needs: Mapped[str | None] = mapped_column(String(300), nullable=True)
    preferred_channel: Mapped[str] = mapped_column(String(10))  # push | email
    contact: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(2), default="en")  # en | es
    typical_departure_time: Mapped[str] = mapped_column(String(5))  # "HH:MM" local


class DailySchedule(Base):
    __tablename__ = "daily_schedules"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(10))  # onsite | telework | leave


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    facility_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16))  # nws | nwps | modot | roster
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


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    manager_name: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(10))  # approve | override
    final_posture: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"))
    included: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(10))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


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

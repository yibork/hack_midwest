"""Demo roles (no real auth): the header role switcher sends X-Role and X-Person.

facility_manager sees everything. supervisor: own team only. security_desk: the access list
only. employee: their own status only. Nobody but the manager sees reason details beyond
audience_text, and nobody but the person sees their accommodation needs.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, Query
from sqlalchemy.orm import Session

from . import models
from .db import get_db

ROLES = ("facility_manager", "supervisor", "employee", "security_desk", "auditor")


@dataclass(frozen=True)
class Viewer:
    role: str
    person_id: str | None


def viewer(x_role: str | None = Header(default=None), x_person: str | None = Header(default=None),
           query_role: str | None = Query(default=None, alias="role"), query_person: str | None = Query(default=None, alias="person"),
           db: Session = Depends(get_db)) -> Viewer:
    role = x_role or query_role or "facility_manager"  # demo only, not authentication
    x_person = x_person or query_person
    if role not in ROLES:
        raise HTTPException(400, f"unknown role {role!r}")
    if role in ("supervisor", "employee"):
        person = db.get(models.Employee, x_person) if x_person else None
        if person is None:
            raise HTTPException(400, "X-Person (an employee id) is required for this role")
        if role == "supervisor" and person.role not in ("supervisor", "facility_manager"):
            raise HTTPException(403, "that person is not a supervisor")
    return Viewer(role=role, person_id=x_person)


def require(*roles: str):
    def dep(v: Viewer = Depends(viewer)) -> Viewer:
        if v.role not in roles:
            raise HTTPException(403, f"not available to the {v.role} role")
        return v
    return dep


manager_only = require("facility_manager")
audit_reader = require("facility_manager", "auditor")


def manager_or_auditor_read(request: Request, v: Viewer = Depends(viewer)):
    import re
    if v.role == "facility_manager":
        return v
    allowed = re.fullmatch(r"/api/(?:facilities(?:/[^/]+)?|thresholds|record(?:/at|/verify)?|snapshots/[a-f0-9]{64}(?:/verify)?|evidence/[^/]+)", request.url.path)
    if v.role != "auditor" or request.method != "GET" or not allowed:
        raise HTTPException(403, "Read-only auditor routes are Record, memos and Thresholds")
    return v


def audit_payload(value):
    from .memo import public_text
    if isinstance(value, dict):
        private = {"home_lat", "home_lon", "home_coordinates", "accommodation_needs", "message", "duty_instruction", "reason_text", "token"}
        return {k: audit_payload(v) for k, v in value.items() if k not in private}
    if isinstance(value, list):
        return [audit_payload(v) for v in value]
    return public_text(value) if isinstance(value, str) else value

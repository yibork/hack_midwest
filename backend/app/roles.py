"""Demo roles (no real auth): the header role switcher sends X-Role and X-Person.

facility_manager sees everything. supervisor: own team only. security_desk: the access list
only. employee: their own status only. Nobody but the manager sees reason details beyond
audience_text, and nobody but the person sees their accommodation needs.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import models
from .db import get_db

ROLES = ("facility_manager", "supervisor", "security_desk", "employee")


@dataclass(frozen=True)
class Viewer:
    role: str
    person_id: str | None


def viewer(x_role: str | None = Header(default=None), x_person: str | None = Header(default=None),
           db: Session = Depends(get_db)) -> Viewer:
    role = x_role or "facility_manager"  # default keeps the manager pages and scripts working
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

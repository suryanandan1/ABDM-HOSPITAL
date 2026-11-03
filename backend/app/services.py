"""Shared business logic used by more than one route."""

import re
from difflib import SequenceMatcher

from fastapi import HTTPException, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Doctor, Patient, User, utcnow


def audit(db: Session, user: User | None, action: str, entity_type: str | None = None,
          entity_id=None, details: dict | None = None, request: Request | None = None,
          hospital_id: int | None = None) -> None:
    """Add an audit entry to the current transaction (committed together with the change)."""
    db.add(AuditLog(
        hospital_id=hospital_id or (user.hospital_id if user else None),
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        ip_address=request.client.host if request and request.client else None,
    ))


def paginate(db: Session, query, page: int, page_size: int) -> dict:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_or_404(db: Session, model, entity_id: int, hospital_id: int):
    """Fetch a row that belongs to the caller's hospital, or raise 404."""
    obj = db.get(model, entity_id)
    if obj is None or obj.hospital_id != hospital_id:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def changed_fields(obj, data: dict) -> dict:
    """Apply `data` to `obj` and return {field: [old, new]} for values that actually changed."""
    changes = {}
    for field, value in data.items():
        old = getattr(obj, field)
        if old != value:
            changes[field] = [str(old) if old is not None else None, str(value) if value is not None else None]
            setattr(obj, field, value)
    return changes


# ---------- clinical workflow ----------

def current_doctor(db: Session, user: User) -> Doctor:
    """Doctor profile linked to the user; clinical authoring and approval require one."""
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == user.id, Doctor.is_active.is_(True)))
    if doctor is None:
        raise HTTPException(403, "Your account is not linked to an active doctor profile")
    return doctor


def require_status(record, *allowed: str, action: str) -> None:
    if record.status not in allowed:
        raise HTTPException(409, f"Cannot {action}: record is {record.status.replace('_', ' ')}")


def mark_approved(record, user: User) -> None:
    record.status = "approved"
    record.status_reason = None
    record.approved_by_id = user.id
    record.approved_at = utcnow()


# ---------- patient duplicate detection ----------

def _normalize_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z\s]", " ", name.lower()).split())


def name_similarity(a: str, b: str) -> float:
    a, b = _normalize_name(a), _normalize_name(b)
    direct = SequenceMatcher(None, a, b).ratio()
    token_sorted = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(direct, token_sorted)


LEVEL_ORDER = {"exact": 0, "likely": 1, "possible": 2}


def find_duplicate_patients(db: Session, hospital_id: int, *, full_name: str, gender: str, mobile: str,
                            year_of_birth: int | None, abha_number: str | None = None,
                            abha_address: str | None = None, exclude_id: int | None = None) -> list[dict]:
    """Return possible existing registrations of the same person, strongest matches first.

    exact:    same ABHA number or ABHA address (never allowed twice in one hospital)
    likely:   same mobile and similar name
    possible: similar name, same gender and year of birth within one year
    """
    conditions = [Patient.mobile == mobile]
    if abha_number:
        conditions.append(Patient.abha_number == abha_number)
    if abha_address:
        conditions.append(Patient.abha_address == abha_address)
    if year_of_birth:
        conditions.append(and_(Patient.gender == gender,
                               Patient.year_of_birth.between(year_of_birth - 1, year_of_birth + 1)))
    query = select(Patient).where(Patient.hospital_id == hospital_id, or_(*conditions))
    if exclude_id:
        query = query.where(Patient.id != exclude_id)

    candidates = []
    for patient in db.scalars(query):
        reasons, level = [], None
        similar_name = name_similarity(patient.full_name, full_name) >= 0.85
        if abha_number and patient.abha_number == abha_number:
            reasons.append("Same ABHA number")
            level = "exact"
        if abha_address and patient.abha_address == abha_address:
            reasons.append("Same ABHA address")
            level = "exact"
        if similar_name:
            reasons.append("Similar name")
        if patient.mobile == mobile:
            reasons.append("Same mobile number")
            if similar_name:
                level = level or "likely"
        if year_of_birth and abs(patient.year_of_birth - year_of_birth) <= 1 and patient.gender == gender:
            reasons.append("Same gender and similar year of birth")
            if similar_name:
                level = level or "possible"
        if level:
            candidates.append({"patient": patient, "level": level, "reasons": reasons})
    return sorted(candidates, key=lambda c: LEVEL_ORDER[c["level"]])

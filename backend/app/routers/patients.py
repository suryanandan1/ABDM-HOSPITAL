import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Hospital, Patient, User, utcnow
from app.schemas import (AbhaVerifyRequest, DuplicateCandidate, Page, PatientCreate, PatientOut,
                         PatientSummary, PatientUpdate)
from app.security import require
from app.services import audit, changed_fields, find_duplicate_patients, get_or_404, paginate

router = APIRouter(prefix="/patients", tags=["patients"])

ABHA_FIELDS = ("abha_number", "abha_address")


def duplicates_for(db: Session, hospital_id: int, body: PatientCreate) -> list[dict]:
    return find_duplicate_patients(
        db, hospital_id, full_name=body.full_name, gender=body.gender, mobile=body.mobile,
        year_of_birth=body.year_of_birth, abha_number=body.abha_number, abha_address=body.abha_address)


def duplicate_conflict(message: str, duplicates: list[dict]) -> HTTPException:
    return HTTPException(409, {
        "message": message,
        "duplicates": [DuplicateCandidate.model_validate(d, from_attributes=True).model_dump(mode="json")
                       for d in duplicates],
    })


def ensure_abha_unique(db: Session, patient: Patient, data: dict) -> None:
    for field in ABHA_FIELDS:
        value = data.get(field)
        if value and db.scalar(select(Patient.id).where(
                Patient.hospital_id == patient.hospital_id, getattr(Patient, field) == value,
                Patient.id != patient.id)):
            raise HTTPException(409, f"Another patient is already registered with this {field.replace('_', ' ')}")


@router.get("", response_model=Page[PatientSummary])
def search_patients(q: str | None = Query(None, description="Patient ID, name, mobile or verified ABHA number/address"),
                    abha_verified: bool | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    user: User = Depends(require("patient:read")), db: Session = Depends(get_db)):
    query = select(Patient).where(Patient.hospital_id == user.hospital_id).order_by(Patient.created_at.desc())
    if q and q.strip():
        term = q.strip()
        digits = re.sub(r"[\s\-]", "", term)
        conditions = [Patient.full_name.ilike(f"%{term}%"), Patient.patient_code.ilike(f"%{term}%")]
        if digits.isdigit() and len(digits) >= 4:
            conditions.append(Patient.mobile.contains(digits[-10:]))
        # ABHA identifiers are only searchable once verified, so unverified entries cannot be used to look people up.
        if digits.isdigit() and len(digits) == 14:
            conditions.append(and_(Patient.abha_number == digits, Patient.abha_verified.is_(True)))
        if "@" in term:
            conditions.append(and_(Patient.abha_address == term.lower(), Patient.abha_verified.is_(True)))
        query = query.where(or_(*conditions))
    if abha_verified is not None:
        query = query.where(Patient.abha_verified == abha_verified)
    return paginate(db, query, page, page_size)


@router.post("/duplicate-check", response_model=list[DuplicateCandidate])
def check_duplicates(body: PatientCreate, user: User = Depends(require("patient:create")),
                     db: Session = Depends(get_db)):
    return duplicates_for(db, user.hospital_id, body)


@router.post("", response_model=PatientOut, status_code=201)
def register_patient(body: PatientCreate, request: Request,
                     user: User = Depends(require("patient:create")), db: Session = Depends(get_db)):
    duplicates = duplicates_for(db, user.hospital_id, body)
    if any(d["level"] == "exact" for d in duplicates):
        raise duplicate_conflict("A patient with this ABHA identifier is already registered.",
                                 [d for d in duplicates if d["level"] == "exact"])
    if duplicates and not body.confirm_not_duplicate:
        raise duplicate_conflict("Possible duplicate patients found. Review them before registering.", duplicates)

    data = body.model_dump(exclude={"age", "confirm_not_duplicate"})
    patient = Patient(hospital_id=user.hospital_id, patient_code=uuid4().hex, year_of_birth=body.year_of_birth,
                      registered_by_id=user.id, **data)
    db.add(patient)
    db.flush()
    patient.patient_code = f"{db.get(Hospital, user.hospital_id).code}-{patient.id:06d}"
    details = {"patient_code": patient.patient_code}
    if duplicates:
        details["duplicate_warning_overridden"] = [d["patient"].patient_code for d in duplicates]
    audit(db, user, "patient.create", "patient", patient.id, details, request)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: int, request: Request,
                user: User = Depends(require("patient:read")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    audit(db, user, "patient.view", "patient", patient.id, request=request)
    db.commit()
    return patient


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: int, body: PatientUpdate, request: Request,
                   user: User = Depends(require("patient:update")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    data = body.model_dump(exclude_unset=True)
    if None in (data.get("full_name", ""), data.get("gender", ""), data.get("mobile", "")):
        raise HTTPException(422, "Name, gender and mobile number cannot be empty")

    if "date_of_birth" in data or "age" in data:
        data.pop("age", None)
        if body.year_of_birth is None:
            raise HTTPException(422, "Provide either date of birth or age")
        data["date_of_birth"] = body.date_of_birth
        data["year_of_birth"] = body.year_of_birth

    ensure_abha_unique(db, patient, data)
    abha_changed = any(field in data and data[field] != getattr(patient, field) for field in ABHA_FIELDS)
    changes = changed_fields(patient, data)
    if abha_changed and patient.abha_verified:
        # A changed identifier has not been verified, whatever the previous state was.
        patient.abha_verified = False
        patient.abha_verified_at = patient.abha_verified_by_id = patient.abha_verification_method = None
        changes["abha_verified"] = ["True", "False"]
    if changes:
        audit(db, user, "patient.update", "patient", patient.id, {"changed_fields": sorted(changes)}, request)
        db.commit()
        db.refresh(patient)
    return patient


@router.post("/{patient_id}/abha/verify", response_model=PatientOut)
def verify_abha(patient_id: int, body: AbhaVerifyRequest, request: Request,
                user: User = Depends(require("abha:verify")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    if not (patient.abha_number or patient.abha_address):
        raise HTTPException(400, "Patient has no ABHA number or ABHA address to verify")
    if patient.abha_verified:
        raise HTTPException(400, "ABHA is already verified")
    patient.abha_verified = True
    patient.abha_verified_at = utcnow()
    patient.abha_verified_by_id = user.id
    patient.abha_verification_method = body.method
    audit(db, user, "patient.abha_verified", "patient", patient.id, {"method": body.method, "note": body.note}, request)
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}/abha/verify", response_model=PatientOut)
def revoke_abha_verification(patient_id: int, request: Request,
                             user: User = Depends(require("abha:verify")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    if not patient.abha_verified:
        raise HTTPException(400, "ABHA is not verified")
    patient.abha_verified = False
    patient.abha_verified_at = patient.abha_verified_by_id = patient.abha_verification_method = None
    audit(db, user, "patient.abha_unverified", "patient", patient.id, request=request)
    db.commit()
    db.refresh(patient)
    return patient

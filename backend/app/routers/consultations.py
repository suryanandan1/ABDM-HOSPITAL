from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.fhir import FREQUENCIES, ROUTES, VITAL_SIGNS
from app.models import Condition, Encounter, LabOrder, Patient, Prescription, User, utcnow
from app.schemas import (ConditionOut, ConditionStatusUpdate, EncounterCreate, EncounterOut, EncounterUpdate, Page,
                         PatientHistory, ReasonRequest)
from app.security import get_current_user, require
from app.services import audit, current_doctor, get_or_404, mark_approved, paginate, require_status

router = APIRouter(tags=["consultations"])


def own_draft(db: Session, user: User, encounter_id: int, action: str) -> Encounter:
    """A draft consultation that the current user (as its doctor) may change."""
    encounter = get_or_404(db, Encounter, encounter_id, user.hospital_id)
    if encounter.doctor_id != current_doctor(db, user).id:
        raise HTTPException(403, "Only the consulting doctor can change this consultation")
    require_status(encounter, "draft", action=action)
    return encounter


@router.get("/terminology")
def terminology(_: User = Depends(get_current_user)):
    """Coded value lists used by the clinical forms (single source shared with the FHIR mapping)."""
    return {
        "vitals": [{"key": k, "label": label, "unit": unit, "min": low, "max": high}
                   for k, (label, _loinc, unit, low, high) in VITAL_SIGNS.items()],
        "routes": [{"key": k, "label": label} for k, (label, *_codes) in ROUTES.items()],
        "frequencies": [{"key": k, "label": label} for k, (label, _timing) in FREQUENCIES.items()],
    }


@router.get("/consultations", response_model=Page[EncounterOut])
def list_consultations(patient_id: int | None = None, status: str | None = None, mine: bool = False,
                       page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    query = select(Encounter).where(Encounter.hospital_id == user.hospital_id).order_by(Encounter.visit_at.desc())
    if patient_id:
        query = query.where(Encounter.patient_id == patient_id)
    if status:
        query = query.where(Encounter.status == status)
    if mine:
        query = query.where(Encounter.doctor_id == current_doctor(db, user).id)
    return paginate(db, query, page, page_size)


@router.post("/consultations", response_model=EncounterOut, status_code=201)
def start_consultation(body: EncounterCreate, request: Request,
                       user: User = Depends(require("consultation:write")), db: Session = Depends(get_db)):
    doctor = current_doctor(db, user)
    patient = get_or_404(db, Patient, body.patient_id, user.hospital_id)
    encounter = Encounter(hospital_id=user.hospital_id, patient_id=patient.id, doctor_id=doctor.id,
                          visit_at=body.visit_at or utcnow(), created_by_id=user.id)
    db.add(encounter)
    db.flush()
    audit(db, user, "consultation.create", "consultation", encounter.id, {"patient_id": patient.id}, request)
    db.commit()
    return encounter


@router.get("/consultations/{encounter_id}", response_model=EncounterOut)
def get_consultation(encounter_id: int, request: Request,
                     user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    encounter = get_or_404(db, Encounter, encounter_id, user.hospital_id)
    audit(db, user, "consultation.view", "consultation", encounter.id, request=request)
    db.commit()
    return encounter


@router.put("/consultations/{encounter_id}", response_model=EncounterOut)
def save_consultation(encounter_id: int, body: EncounterUpdate, request: Request,
                      user: User = Depends(require("consultation:write")), db: Session = Depends(get_db)):
    encounter = own_draft(db, user, encounter_id, "edit consultation")
    data = body.model_dump(exclude_unset=True, exclude={"diagnoses"})
    if "visit_at" in data and data["visit_at"] is None:
        data.pop("visit_at")
    for field, value in data.items():
        setattr(encounter, field, value)
    if body.diagnoses is not None:
        encounter.conditions = [Condition(patient_id=encounter.patient_id, **d.model_dump()) for d in body.diagnoses]
    audit(db, user, "consultation.update", "consultation", encounter.id,
          {"fields": sorted(body.model_fields_set)}, request)
    db.commit()
    db.refresh(encounter)
    return encounter


@router.post("/consultations/{encounter_id}/finalize", response_model=EncounterOut)
def finalize_consultation(encounter_id: int, request: Request,
                          user: User = Depends(require("consultation:write")), db: Session = Depends(get_db)):
    encounter = own_draft(db, user, encounter_id, "finalize consultation")
    if not encounter.chief_complaint:
        raise HTTPException(422, "Chief complaint is required before finalizing")
    mark_approved(encounter, user)
    audit(db, user, "consultation.finalize", "consultation", encounter.id,
          {"diagnoses": len(encounter.conditions)}, request)
    db.commit()
    return encounter


@router.post("/consultations/{encounter_id}/cancel", response_model=EncounterOut)
def cancel_consultation(encounter_id: int, body: ReasonRequest, request: Request,
                        user: User = Depends(require("consultation:write")), db: Session = Depends(get_db)):
    encounter = own_draft(db, user, encounter_id, "cancel consultation")
    encounter.status, encounter.status_reason = "cancelled", body.reason
    audit(db, user, "consultation.cancel", "consultation", encounter.id, {"reason": body.reason}, request)
    db.commit()
    return encounter


@router.patch("/conditions/{condition_id}", response_model=ConditionOut)
def update_condition_status(condition_id: int, body: ConditionStatusUpdate, request: Request,
                            user: User = Depends(require("consultation:write")), db: Session = Depends(get_db)):
    """Mark a diagnosis resolved/active later on; the rest of a finalized diagnosis stays unchanged."""
    current_doctor(db, user)
    condition = db.get(Condition, condition_id)
    if condition is None or condition.encounter.hospital_id != user.hospital_id:
        raise HTTPException(404, "Condition not found")
    old = condition.clinical_status
    condition.clinical_status = body.clinical_status
    audit(db, user, "condition.status_change", "condition", condition.id,
          {"changes": {"clinical_status": [old, body.clinical_status]}}, request)
    db.commit()
    return condition


@router.get("/patients/{patient_id}/history", response_model=PatientHistory)
def patient_history(patient_id: int, request: Request,
                    user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    """Finalized clinical record of a patient: consultations, diagnoses, prescriptions and lab reports."""
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    consultations = db.scalars(select(Encounter).where(
        Encounter.patient_id == patient.id, Encounter.status == "approved").order_by(Encounter.visit_at.desc())).all()
    prescriptions = db.scalars(select(Prescription).where(
        Prescription.patient_id == patient.id, Prescription.status == "approved")
        .order_by(Prescription.prescription_date.desc(), Prescription.id.desc())).all()
    lab_orders = db.scalars(select(LabOrder).where(
        LabOrder.patient_id == patient.id, LabOrder.status == "completed").order_by(LabOrder.created_at.desc())).all()
    active = [c for e in consultations for c in e.conditions if c.clinical_status in ("active", "recurrence")]
    audit(db, user, "patient.history_view", "patient", patient.id, request=request)
    db.commit()
    return {"patient": patient, "active_conditions": active, "consultations": consultations,
            "prescriptions": prescriptions, "lab_orders": lab_orders}

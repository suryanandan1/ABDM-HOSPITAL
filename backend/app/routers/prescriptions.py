from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Encounter, Prescription, PrescriptionItem, User
from app.schemas import Page, PrescriptionCreate, PrescriptionIn, PrescriptionOut, ReasonRequest
from app.security import require
from app.services import audit, current_doctor, get_or_404, mark_approved, paginate, require_status

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])

# draft/rejected -> (submit) pending_approval -> (approve) approved | (reject) rejected; any open state -> cancelled


def own_prescription(db: Session, user: User, prescription_id: int, *allowed: str, action: str) -> Prescription:
    """Prescription in one of the `allowed` states that belongs to the current user's doctor profile."""
    prescription = get_or_404(db, Prescription, prescription_id, user.hospital_id)
    if prescription.doctor_id != current_doctor(db, user).id:
        raise HTTPException(403, "Only the prescribing doctor can change or approve this prescription")
    require_status(prescription, *allowed, action=action)
    return prescription


def set_items(prescription: Prescription, body: PrescriptionIn) -> None:
    prescription.items = [PrescriptionItem(**item.model_dump()) for item in body.items]
    prescription.notes = body.notes


@router.get("", response_model=Page[PrescriptionOut])
def list_prescriptions(patient_id: int | None = None, encounter_id: int | None = None, status: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    query = select(Prescription).where(Prescription.hospital_id == user.hospital_id).order_by(Prescription.id.desc())
    for column, value in ((Prescription.patient_id, patient_id), (Prescription.encounter_id, encounter_id),
                          (Prescription.status, status)):
        if value:
            query = query.where(column == value)
    return paginate(db, query, page, page_size)


@router.post("", response_model=PrescriptionOut, status_code=201)
def create_prescription(body: PrescriptionCreate, request: Request,
                        user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    doctor = current_doctor(db, user)
    encounter = get_or_404(db, Encounter, body.encounter_id, user.hospital_id)
    if encounter.status == "cancelled":
        raise HTTPException(409, "Cannot prescribe on a cancelled consultation")
    prescription = Prescription(hospital_id=user.hospital_id, patient_id=encounter.patient_id,
                                encounter_id=encounter.id, doctor_id=doctor.id, created_by_id=user.id)
    set_items(prescription, body)
    db.add(prescription)
    db.flush()
    audit(db, user, "prescription.create", "prescription", prescription.id,
          {"encounter_id": encounter.id, "medicines": len(body.items)}, request)
    db.commit()
    return prescription


@router.get("/{prescription_id}", response_model=PrescriptionOut)
def get_prescription(prescription_id: int, user: User = Depends(require("clinical:read")),
                     db: Session = Depends(get_db)):
    return get_or_404(db, Prescription, prescription_id, user.hospital_id)


@router.put("/{prescription_id}", response_model=PrescriptionOut)
def update_prescription(prescription_id: int, body: PrescriptionIn, request: Request,
                        user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    prescription = own_prescription(db, user, prescription_id, "draft", "rejected", action="edit prescription")
    set_items(prescription, body)
    prescription.status = "draft"
    audit(db, user, "prescription.update", "prescription", prescription.id,
          {"medicines": len(body.items)}, request)
    db.commit()
    db.refresh(prescription)
    return prescription


@router.post("/{prescription_id}/submit", response_model=PrescriptionOut)
def submit_prescription(prescription_id: int, request: Request,
                        user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    prescription = own_prescription(db, user, prescription_id, "draft", "rejected", action="submit prescription")
    prescription.status, prescription.status_reason = "pending_approval", None
    audit(db, user, "prescription.submit", "prescription", prescription.id, request=request)
    db.commit()
    return prescription


@router.post("/{prescription_id}/approve", response_model=PrescriptionOut)
def approve_prescription(prescription_id: int, request: Request,
                         user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    prescription = own_prescription(db, user, prescription_id, "pending_approval", action="approve prescription")
    mark_approved(prescription, user)
    audit(db, user, "prescription.approve", "prescription", prescription.id, request=request)
    db.commit()
    return prescription


@router.post("/{prescription_id}/reject", response_model=PrescriptionOut)
def reject_prescription(prescription_id: int, body: ReasonRequest, request: Request,
                        user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    prescription = own_prescription(db, user, prescription_id, "pending_approval", action="reject prescription")
    prescription.status, prescription.status_reason = "rejected", body.reason
    audit(db, user, "prescription.reject", "prescription", prescription.id, {"reason": body.reason}, request)
    db.commit()
    return prescription


@router.post("/{prescription_id}/cancel", response_model=PrescriptionOut)
def cancel_prescription(prescription_id: int, body: ReasonRequest, request: Request,
                        user: User = Depends(require("prescription:write")), db: Session = Depends(get_db)):
    prescription = own_prescription(db, user, prescription_id, "draft", "pending_approval", "rejected", "approved",
                                    action="cancel prescription")
    previous = prescription.status
    prescription.status, prescription.status_reason = "cancelled", body.reason
    audit(db, user, "prescription.cancel", "prescription", prescription.id,
          {"reason": body.reason, "previous_status": previous}, request)
    db.commit()
    return prescription

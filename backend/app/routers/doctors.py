from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Doctor, User
from app.schemas import DoctorCreate, DoctorOut, DoctorUpdate, Page
from app.security import require
from app.services import audit, changed_fields, get_or_404, paginate

router = APIRouter(prefix="/doctors", tags=["doctors"])


def validate_doctor(db: Session, hospital_id: int, data: dict, doctor_id: int | None = None) -> None:
    """Check unique registration number and that a linked user is a free doctor account."""
    if data.get("registration_number") and db.scalar(select(Doctor.id).where(
            Doctor.hospital_id == hospital_id, Doctor.registration_number == data["registration_number"],
            Doctor.id != (doctor_id or 0))):
        raise HTTPException(409, "A doctor with this registration number already exists")
    if data.get("user_id"):
        linked = db.get(User, data["user_id"])
        if linked is None or linked.hospital_id != hospital_id or linked.role_code != "doctor":
            raise HTTPException(400, "Linked user must be a staff account with the Doctor role")
        if db.scalar(select(Doctor.id).where(Doctor.user_id == linked.id, Doctor.id != (doctor_id or 0))):
            raise HTTPException(409, "This user account is already linked to another doctor")


@router.get("", response_model=Page[DoctorOut])
def list_doctors(q: str | None = None, is_active: bool | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 user: User = Depends(require("doctor:read")), db: Session = Depends(get_db)):
    query = select(Doctor).where(Doctor.hospital_id == user.hospital_id).order_by(Doctor.full_name)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Doctor.full_name.ilike(like), Doctor.specialization.ilike(like),
                                Doctor.department.ilike(like), Doctor.registration_number.ilike(like)))
    if is_active is not None:
        query = query.where(Doctor.is_active == is_active)
    return paginate(db, query, page, page_size)


@router.get("/{doctor_id}", response_model=DoctorOut)
def get_doctor(doctor_id: int, user: User = Depends(require("doctor:read")), db: Session = Depends(get_db)):
    return get_or_404(db, Doctor, doctor_id, user.hospital_id)


@router.post("", response_model=DoctorOut, status_code=201)
def create_doctor(body: DoctorCreate, request: Request,
                  user: User = Depends(require("doctor:manage")), db: Session = Depends(get_db)):
    data = body.model_dump()
    validate_doctor(db, user.hospital_id, data)
    doctor = Doctor(hospital_id=user.hospital_id, **data)
    db.add(doctor)
    db.flush()
    audit(db, user, "doctor.create", "doctor", doctor.id,
          {"name": doctor.full_name, "registration_number": doctor.registration_number}, request)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.patch("/{doctor_id}", response_model=DoctorOut)
def update_doctor(doctor_id: int, body: DoctorUpdate, request: Request,
                  user: User = Depends(require("doctor:manage")), db: Session = Depends(get_db)):
    doctor = get_or_404(db, Doctor, doctor_id, user.hospital_id)
    data = body.model_dump(exclude_unset=True)
    if None in (data.get("full_name", ""), data.get("registration_number", ""), data.get("is_active", "")):
        raise HTTPException(422, "Name, registration number and status cannot be empty")
    validate_doctor(db, user.hospital_id, data, doctor.id)
    changes = changed_fields(doctor, data)
    if changes:
        audit(db, user, "doctor.update", "doctor", doctor.id, {"changes": changes}, request)
        db.commit()
        db.refresh(doctor)
    return doctor

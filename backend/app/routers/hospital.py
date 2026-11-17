from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Hospital, User
from app.schemas import HospitalOut, HospitalUpdate
from app.security import require
from app.services import audit, changed_fields

router = APIRouter(prefix="/hospital", tags=["hospital"])


def check_unique_ids(db: Session, data: dict, hospital_id: int | None) -> None:
    """HFR, HIP and HIU IDs identify one hospital each (hospital_id: the hospital being changed, None if new)."""
    for field, label in (("hfr_id", "HFR ID"), ("abdm_hip_id", "ABDM HIP ID"), ("abdm_hiu_id", "ABDM HIU ID")):
        column = getattr(Hospital, field)
        if data.get(field) and db.scalar(select(Hospital.id).where(column == data[field], Hospital.id != (hospital_id or 0))):
            raise HTTPException(409, f"This {label} is already used by another hospital")


def update_hospital_profile(db: Session, hospital: Hospital, data: dict, actor: User, request: Request) -> Hospital:
    if data.get("name", "") is None:
        raise HTTPException(422, "Hospital name is required")
    check_unique_ids(db, data, hospital.id)
    changes = changed_fields(hospital, data)
    if changes:
        audit(db, actor, "hospital.update", "hospital", hospital.id, {"changes": changes}, request,
              hospital_id=hospital.id)
        db.commit()
    return hospital


@router.get("", response_model=HospitalOut)
def get_hospital(user: User = Depends(require("hospital:read"))):
    return user.hospital


@router.put("", response_model=HospitalOut)
def update_hospital(body: HospitalUpdate, request: Request,
                    user: User = Depends(require("hospital:update")), db: Session = Depends(get_db)):
    return update_hospital_profile(db, db.get(Hospital, user.hospital_id), body.model_dump(exclude_unset=True),
                                   user, request)

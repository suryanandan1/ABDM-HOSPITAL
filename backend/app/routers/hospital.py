from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Hospital, User
from app.schemas import HospitalOut, HospitalUpdate
from app.security import require
from app.services import audit, changed_fields

router = APIRouter(prefix="/hospital", tags=["hospital"])


@router.get("", response_model=HospitalOut)
def get_hospital(user: User = Depends(require("hospital:read"))):
    return user.hospital


@router.put("", response_model=HospitalOut)
def update_hospital(body: HospitalUpdate, request: Request,
                    user: User = Depends(require("hospital:update")), db: Session = Depends(get_db)):
    hospital = db.get(Hospital, user.hospital_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name", "") is None:
        raise HTTPException(422, "Hospital name is required")
    if data.get("hfr_id") and db.scalar(select(Hospital.id).where(
            Hospital.hfr_id == data["hfr_id"], Hospital.id != hospital.id)):
        raise HTTPException(409, "This HFR ID is already used by another hospital")
    changes = changed_fields(hospital, data)
    if changes:
        audit(db, user, "hospital.update", "hospital", hospital.id, {"changes": changes}, request)
        db.commit()
    return hospital

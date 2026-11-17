"""Network administration: every hospital on this platform and their staff accounts.

Only the network administrator (role network_admin, permission network:manage) uses these. A network administrator
belongs to no hospital and has no patient or clinical access: each hospital's own staff remain the only people who
see its patients, and records move between hospitals only through ABDM with the patient's consent. Every change is
written to the audit log of the hospital it concerns, so that hospital's administrator sees it too.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import CareContext, Doctor, Hospital, Patient, Role, User
from app.routers.hospital import check_unique_ids, update_hospital_profile
from app.routers.staff import (create_staff_account, hospital_roles, reset_staff_two_step, staff_query,
                               update_staff_account)
from app.schemas import (NetworkHospitalCreate, NetworkHospitalOut, NetworkHospitalUpdate, Page, RoleCode, RoleOut,
                         StaffCreate, StaffUpdate, UserOut)
from app.security import require, revoke_tokens
from app.services import audit, paginate

router = APIRouter(prefix="/network", tags=["network administration"])
network_admin = require("network:manage")


def hospital_or_404(db: Session, hospital_id: int) -> Hospital:
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(404, "Hospital not found")
    return hospital


def staff_or_404(db: Session, user_id: int) -> User:
    staff = db.get(User, user_id)
    if staff is None or staff.hospital_id is None:  # network administrators are not managed here
        raise HTTPException(404, "Staff member not found")
    return staff


def hospital_out(db: Session, hospital: Hospital) -> NetworkHospitalOut:
    def count(query):
        return db.scalar(query) or 0

    staff = select(func.count(User.id)).where(User.hospital_id == hospital.id)
    admins = db.scalars(select(User).join(Role).where(User.hospital_id == hospital.id, Role.code == "hospital_admin")
                        .order_by(User.username)).all()
    return NetworkHospitalOut.model_validate({
        **{c.name: getattr(hospital, c.name) for c in Hospital.__table__.columns},
        "stats": {
            "staff": count(staff),
            "active_staff": count(staff.where(User.is_active.is_(True))),
            "doctors": count(select(func.count(Doctor.id)).where(Doctor.hospital_id == hospital.id,
                                                                  Doctor.is_active.is_(True))),
            "patients": count(select(func.count(Patient.id)).where(Patient.hospital_id == hospital.id)),
            "shared_records": count(select(func.count(CareContext.id)).where(
                CareContext.hospital_id == hospital.id, CareContext.link_status == "linked")),
        },
        "admins": admins,
    })


@router.get("/hospitals", response_model=list[NetworkHospitalOut])
def list_hospitals(_: User = Depends(network_admin), db: Session = Depends(get_db)):
    return [hospital_out(db, h) for h in db.scalars(select(Hospital).order_by(Hospital.id))]


@router.get("/hospitals/{hospital_id}", response_model=NetworkHospitalOut)
def get_hospital(hospital_id: int, _: User = Depends(network_admin), db: Session = Depends(get_db)):
    return hospital_out(db, hospital_or_404(db, hospital_id))


@router.post("/hospitals", response_model=NetworkHospitalOut, status_code=201)
def create_hospital(body: NetworkHospitalCreate, request: Request,
                    user: User = Depends(network_admin), db: Session = Depends(get_db)):
    """Add a hospital, optionally with its administrator account. In simulation it gets SIM_HIP_<code> and
    SIM_HIU_<code> unless other ABDM IDs are given, so it can exchange records with the others right away."""
    data = body.model_dump(exclude={"admin"})
    data["code"] = data["code"].upper()
    if db.scalar(select(Hospital.id).where(func.upper(Hospital.code) == data["code"])):
        raise HTTPException(409, "This hospital code is already used")
    if settings.abdm_mode == "simulation":
        data["abdm_hip_id"] = data["abdm_hip_id"] or f"SIM_HIP_{data['code']}"
        data["abdm_hiu_id"] = data["abdm_hiu_id"] or f"SIM_HIU_{data['code']}"
    check_unique_ids(db, data, None)
    if body.admin and db.scalar(select(User.id).where(User.username == body.admin.username.lower())):
        raise HTTPException(409, f"Username {body.admin.username.lower()} is already taken")
    hospital = Hospital(**data)
    db.add(hospital)
    db.flush()
    audit(db, user, "hospital.create", "hospital", hospital.id, {"code": hospital.code, "name": hospital.name},
          request, hospital_id=hospital.id)
    db.commit()
    if body.admin:
        create_staff_account(db, hospital.id, StaffCreate(role="hospital_admin", **body.admin.model_dump()),
                             user, request)
    return hospital_out(db, hospital)


@router.patch("/hospitals/{hospital_id}", response_model=NetworkHospitalOut)
def update_hospital(hospital_id: int, body: NetworkHospitalUpdate, request: Request,
                    user: User = Depends(network_admin), db: Session = Depends(get_db)):
    hospital = hospital_or_404(db, hospital_id)
    data = body.model_dump(exclude_unset=True)
    active = data.pop("is_active", None)
    update_hospital_profile(db, hospital, data, user, request)
    if active is not None and active != hospital.is_active:
        hospital.is_active = active
        if not active:  # everyone signed in there is signed out now
            for staff in db.scalars(select(User).where(User.hospital_id == hospital.id)):
                revoke_tokens(staff)
        audit(db, user, "hospital.activate" if active else "hospital.deactivate", "hospital", hospital.id,
              request=request, hospital_id=hospital.id)
        db.commit()
    return hospital_out(db, hospital)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: User = Depends(network_admin), db: Session = Depends(get_db)):
    return hospital_roles(db)


@router.get("/hospitals/{hospital_id}/staff", response_model=Page[UserOut])
def list_staff(hospital_id: int, q: str | None = None, role: RoleCode | None = None, is_active: bool | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               _: User = Depends(network_admin), db: Session = Depends(get_db)):
    hospital_or_404(db, hospital_id)
    return paginate(db, staff_query(hospital_id, q, role, is_active), page, page_size)


@router.post("/hospitals/{hospital_id}/staff", response_model=UserOut, status_code=201)
def create_staff(hospital_id: int, body: StaffCreate, request: Request,
                 user: User = Depends(network_admin), db: Session = Depends(get_db)):
    return create_staff_account(db, hospital_or_404(db, hospital_id).id, body, user, request)


@router.patch("/staff/{user_id}", response_model=UserOut)
def update_staff(user_id: int, body: StaffUpdate, request: Request,
                 user: User = Depends(network_admin), db: Session = Depends(get_db)):
    return update_staff_account(db, staff_or_404(db, user_id), body, user, request)


@router.delete("/staff/{user_id}/mfa", response_model=UserOut)
def reset_staff_mfa(user_id: int, request: Request, user: User = Depends(network_admin),
                    db: Session = Depends(get_db)):
    return reset_staff_two_step(db, staff_or_404(db, user_id), user, request)

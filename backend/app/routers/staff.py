from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User
from app.schemas import Page, RoleOut, RoleCode, StaffCreate, StaffUpdate, UserOut
from app.security import ROLES, hash_password, require
from app.services import audit, changed_fields, get_or_404, paginate

router = APIRouter(tags=["staff"])


def role_by_code(db: Session, code: str) -> Role:
    return db.scalar(select(Role).where(Role.code == code))


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: User = Depends(require("staff:read")), db: Session = Depends(get_db)):
    return [RoleOut(code=r.code, name=r.name, description=r.description,
                    permissions=sorted(ROLES[r.code]["permissions"]))
            for r in db.scalars(select(Role).order_by(Role.id))]


@router.get("/staff", response_model=Page[UserOut])
def list_staff(q: str | None = None, role: RoleCode | None = None, is_active: bool | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               user: User = Depends(require("staff:read")), db: Session = Depends(get_db)):
    query = select(User).where(User.hospital_id == user.hospital_id).order_by(User.full_name)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(User.full_name.ilike(like), User.username.ilike(like), User.email.ilike(like)))
    if role:
        query = query.join(Role).where(Role.code == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    return paginate(db, query, page, page_size)


@router.get("/staff/{user_id}", response_model=UserOut)
def get_staff(user_id: int, user: User = Depends(require("staff:read")), db: Session = Depends(get_db)):
    return get_or_404(db, User, user_id, user.hospital_id)


@router.post("/staff", response_model=UserOut, status_code=201)
def create_staff(body: StaffCreate, request: Request,
                 user: User = Depends(require("staff:manage")), db: Session = Depends(get_db)):
    username = body.username.lower()
    if db.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(409, "Username is already taken")
    staff = User(hospital_id=user.hospital_id, role_id=role_by_code(db, body.role).id, username=username,
                 password_hash=hash_password(body.password), full_name=body.full_name,
                 email=body.email, phone=body.phone)
    db.add(staff)
    db.flush()
    audit(db, user, "staff.create", "user", staff.id, {"username": username, "role": body.role}, request)
    db.commit()
    db.refresh(staff)
    return staff


@router.patch("/staff/{user_id}", response_model=UserOut)
def update_staff(user_id: int, body: StaffUpdate, request: Request,
                 user: User = Depends(require("staff:manage")), db: Session = Depends(get_db)):
    staff = get_or_404(db, User, user_id, user.hospital_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("full_name", "") is None:
        raise HTTPException(422, "Full name is required")
    if staff.id == user.id and (("role" in data and data["role"] != staff.role_code) or data.get("is_active") is False):
        raise HTTPException(400, "You cannot change your own role or deactivate your own account")

    changes = {}
    password = data.pop("password", None)
    if password:
        staff.password_hash = hash_password(password)
        changes["password"] = "reset"
    role = data.pop("role", None)
    if role and role != staff.role_code:
        changes["role"] = [staff.role_code, role]
        staff.role = role_by_code(db, role)
    changes.update(changed_fields(staff, data))
    if changes:
        audit(db, user, "staff.update", "user", staff.id, {"changes": changes}, request)
        db.commit()
    return staff

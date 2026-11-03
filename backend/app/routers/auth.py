import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.database import get_db
from app.models import Doctor, User, utcnow
from app.schemas import ChangePasswordRequest, CurrentUser, LoginRequest, TokenResponse, UserOut
from app.security import create_access_token, get_current_user, hash_password, permissions_for, verify_password
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILURES, WINDOW_SECONDS = 5, 15 * 60
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
_DUMMY_HASH = hash_password("timing-equaliser-not-a-password1")


def recent_failures(username: str) -> int:
    with _failures_lock:
        stamps = [t for t in _failures.get(username, []) if t > time.time() - WINDOW_SECONDS]
        _failures[username] = stamps
        return len(stamps)


def note_failure(username: str) -> None:
    with _failures_lock:
        _failures.setdefault(username, []).append(time.time())


def current_user_out(user: User) -> CurrentUser:
    return CurrentUser(
        **UserOut.model_validate(user).model_dump(),
        hospital_name=user.hospital.name,
        permissions=sorted(permissions_for(user)),
        doctor_id=object_session(user).scalar(
            select(Doctor.id).where(Doctor.user_id == user.id, Doctor.is_active.is_(True))),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = body.username.strip().lower()
    if recent_failures(username) >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.username == username))
    # Unknown users cost the same hash check, so timing does not reveal which usernames exist.
    if not verify_password(body.password, user.password_hash if user else _DUMMY_HASH) or user is None:
        note_failure(username)
        audit(db, None, "auth.login_failed", "user", details={"username": body.username[:50]},
              request=request, hospital_id=user.hospital_id if user else None)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    if not user.is_active or not user.hospital.is_active:
        raise HTTPException(403, "This account is disabled. Contact your hospital administrator.")
    with _failures_lock:
        _failures.pop(username, None)  # a successful sign-in resets the counter
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", "user", user.id, request=request)
    db.commit()
    return TokenResponse(access_token=create_access_token(user), user=current_user_out(user))


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user)):
    return current_user_out(user)


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordRequest, request: Request,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "auth.password_changed", "user", user.id, request=request)
    db.commit()

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, object_session

from app import mfa
from app.config import settings
from app.database import get_db
from app.models import Doctor, LoginFailure, User, utcnow
from app.schemas import (ChangePasswordRequest, CurrentUser, LoginRequest, MfaCodeRequest, MfaDisableRequest,
                         MfaSetupOut, TokenResponse, UserOut)
from app.security import (clear_session_cookies, create_session, end_session, get_session_user, hash_password,
                          mfa_setup_required, needs_rehash, permissions_for, revoke_tokens, set_session_cookies,
                          verify_password)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Sign-in throttling, counted over the last 15 minutes and stored in login_failures (shared by every worker,
# kept across restarts). Limits are per username *and* address first, so someone guessing a doctor's password
# from one computer does not lock the doctor out on theirs; the per-address and per-username limits stop one
# address trying many accounts and many addresses trying one account.
WINDOW = timedelta(minutes=15)
MAX_PER_ACCOUNT_AND_IP, MAX_PER_IP, MAX_PER_ACCOUNT = 5, 20, 50
TOO_MANY = "Too many failed sign-in attempts. Try again in 15 minutes."
_DUMMY_HASH = hash_password("timing-equaliser-not-a-password1")


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers --forwarded-allow-ips=<proxy> so this is the client.
    return (request.client.host if request.client else None) or "unknown"


def is_throttled(db: Session, username: str, ip: str) -> bool:
    since = utcnow() - WINDOW
    recent = select(func.count()).select_from(LoginFailure).where(LoginFailure.created_at > since)
    return (db.scalar(recent.where(LoginFailure.username == username, LoginFailure.ip_address == ip))
            >= MAX_PER_ACCOUNT_AND_IP
            or db.scalar(recent.where(LoginFailure.ip_address == ip)) >= MAX_PER_IP
            or db.scalar(recent.where(LoginFailure.username == username)) >= MAX_PER_ACCOUNT)


def note_failure(db: Session, username: str, ip: str) -> None:
    db.execute(delete(LoginFailure).where(LoginFailure.created_at <= utcnow() - WINDOW))  # forget old attempts
    db.add(LoginFailure(username=username[:50], ip_address=ip[:50]))


def current_user_out(user: User) -> CurrentUser:
    return CurrentUser(
        **UserOut.model_validate(user).model_dump(),
        hospital_name=user.hospital.name,
        permissions=sorted(permissions_for(user)),
        doctor_id=object_session(user).scalar(
            select(Doctor.id).where(Doctor.user_id == user.id, Doctor.is_active.is_(True))),
        mfa_setup_required=mfa_setup_required(user),
        session_idle_minutes=settings.session_idle_minutes,
    )


def start_session(db: Session, user: User, response: Response) -> TokenResponse:
    """Create a session, commit, and hand it out as a cookie (browser) and in the body (API clients)."""
    token = create_session(db, user)
    db.commit()
    set_session_cookies(response, token)
    return TokenResponse(access_token=token, user=current_user_out(user))


@router.post("/login", response_model=TokenResponse,
             responses={401: {"description": "Wrong credentials, or detail.code = mfa_required / mfa_invalid"}})
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    username, ip = body.username.strip().lower(), client_ip(request)
    if is_throttled(db, username, ip):
        raise HTTPException(429, TOO_MANY)
    user = db.scalar(select(User).where(User.username == username))
    # Unknown users cost the same hash check, so timing does not reveal which usernames exist.
    if not verify_password(body.password, user.password_hash if user else _DUMMY_HASH) or user is None:
        note_failure(db, username, ip)
        audit(db, None, "auth.login_failed", "user", details={"username": body.username[:50]},
              request=request, hospital_id=user.hospital_id if user else None)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    if not user.is_active or not user.hospital.is_active:
        raise HTTPException(403, "This account is disabled. Contact your hospital administrator.")
    if user.mfa_enabled:
        if not body.otp:
            raise HTTPException(401, {"code": "mfa_required",
                                      "message": "Enter the 6-digit code from your authenticator app"})
        if not mfa.verify(user, body.otp):
            note_failure(db, username, ip)
            audit(db, user, "auth.mfa_failed", "user", user.id, request=request)
            db.commit()
            raise HTTPException(401, {"code": "mfa_invalid", "message": "That code is not valid. Try the current one."})
    db.execute(delete(LoginFailure).where(LoginFailure.username == username))  # success resets the counters
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)  # upgrade to the current scrypt cost
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", "user", user.id, request=request)
    return start_session(db, user, response)


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_session_user)):
    return current_user_out(user)


@router.post("/change-password", response_model=TokenResponse)
def change_password(body: ChangePasswordRequest, request: Request, response: Response,
                    user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    revoke_tokens(user)  # every other session (e.g. a stolen token) ends; this one continues with a new session
    audit(db, user, "auth.password_changed", "user", user.id, request=request)
    return start_session(db, user, response)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(get_session_user),
           db: Session = Depends(get_db)):
    """Sign out of this session only; the user's other sessions (other computers) continue."""
    end_session(db, request.state.session_id)
    audit(db, user, "auth.logout", "user", user.id, request=request)
    db.commit()
    clear_session_cookies(response)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, user: User = Depends(get_session_user),
               db: Session = Depends(get_db)):
    """Sign out everywhere: every session of this user ends, on every computer."""
    revoke_tokens(user)
    audit(db, user, "auth.logout_all", "user", user.id, request=request)
    db.commit()
    clear_session_cookies(response)


# ---------- two-step sign-in (authenticator app) ----------

@router.post("/mfa/setup", response_model=MfaSetupOut)
def mfa_setup(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    """Create a new secret to add to an authenticator app; it is used only after /mfa/enable confirms a code."""
    if user.mfa_enabled:
        raise HTTPException(400, "Two-step sign-in is already on. Turn it off first to move it to a new app.")
    secret = mfa.new_secret()
    mfa.store_secret(user, secret)
    db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=mfa.provisioning_uri(user, secret))


@router.post("/mfa/enable", response_model=TokenResponse)
def mfa_enable(body: MfaCodeRequest, request: Request, response: Response,
               user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if user.mfa_enabled:
        raise HTTPException(400, "Two-step sign-in is already on")
    if not user.mfa_secret:
        raise HTTPException(400, "Start the setup first")
    if not mfa.verify(user, body.code):
        raise HTTPException(400, "That code is not valid. Check the app shows this account and try the current code.")
    user.mfa_enabled = True
    revoke_tokens(user)  # sessions opened without the second step end; this one continues
    audit(db, user, "auth.mfa_enabled", "user", user.id, request=request)
    return start_session(db, user, response)


@router.post("/mfa/disable", status_code=204)
def mfa_disable(body: MfaDisableRequest, request: Request, user: User = Depends(get_session_user),
                db: Session = Depends(get_db)):
    if not user.mfa_enabled:
        raise HTTPException(400, "Two-step sign-in is not on")
    if user.role.code in settings.mfa_required_role_list:
        raise HTTPException(400, "Your role requires two-step sign-in; an administrator can reset it if you lost "
                                 "your authenticator app")
    if not verify_password(body.password, user.password_hash) or not mfa.verify(user, body.code):
        raise HTTPException(400, "Password or code is incorrect")
    mfa.clear(user)
    audit(db, user, "auth.mfa_disabled", "user", user.id, request=request)
    db.commit()

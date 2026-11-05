"""Password hashing, sessions (JWT + cookie), roles/permissions and the auth dependencies."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import update
from sqlalchemy.orm import Session, object_session

from app.config import settings
from app.database import get_db
from app.models import User, UserSession, utcnow

# Permissions are defined in code (single source of truth); the roles table mirrors ROLES.
ALL_PERMISSIONS = {
    "dashboard:view",
    "hospital:read", "hospital:update",
    "staff:read", "staff:manage",
    "doctor:read", "doctor:manage",
    "patient:read", "patient:create", "patient:update",
    "abha:verify",
    "audit:read",
    # Phase 2 clinical permissions. Finalizing or approving clinical records additionally
    # requires the user's account to be linked to an active doctor profile.
    "clinical:read", "consultation:write", "prescription:write",
    "lab:read", "lab:catalog", "lab:order", "lab:sample", "lab:result", "lab:approve",
    # Phase 3: AI document processing, terminology mapping and FHIR records.
    "document:read", "document:write", "document:approve", "terminology:confirm", "fhir:read", "fhir:generate",
    # Phase 4: ABDM HIP (configure = gateway session and bridge URL).
    "hip:read", "hip:manage", "hip:configure",
    # Phase 5: ABDM HIU (records = viewing clinical content received from other providers).
    "hiu:read", "hiu:request", "hiu:records",
}

ROLES: dict[str, dict] = {
    "hospital_admin": {
        "name": "Hospital Administrator",
        "description": "Manages hospital profile, staff, doctors and has full access.",
        "permissions": ALL_PERMISSIONS,
    },
    "doctor": {
        "name": "Doctor",
        "description": "Consults patients, prescribes, orders tests and approves lab reports.",
        "permissions": {"dashboard:view", "hospital:read", "doctor:read", "patient:read",
                        "clinical:read", "consultation:write", "prescription:write",
                        "lab:read", "lab:order", "lab:approve", "document:read", "document:write",
                        "document:approve", "terminology:confirm", "fhir:read", "fhir:generate",
                        "hiu:read", "hiu:request", "hiu:records"},
    },
    "receptionist": {
        "name": "Receptionist",
        "description": "Registers, searches and updates patient demographics.",
        "permissions": {"dashboard:view", "hospital:read", "doctor:read",
                        "patient:read", "patient:create", "patient:update"},
    },
    "lab_technician": {
        "name": "Laboratory Technician",
        "description": "Manages the test catalogue, collects samples and enters results.",
        "permissions": {"dashboard:view", "hospital:read", "patient:read",
                        "lab:read", "lab:catalog", "lab:sample", "lab:result",
                        "document:read", "document:write", "terminology:confirm"},
    },
    "health_info_staff": {
        "name": "Authorized Health Information Staff",
        "description": "Manages health identifiers (ABHA) and reviews audit trails.",
        "permissions": {"dashboard:view", "hospital:read", "doctor:read", "patient:read",
                        "patient:update", "abha:verify", "audit:read", "clinical:read", "lab:read",
                        "document:read", "document:write", "terminology:confirm", "fhir:read", "fhir:generate",
                        "hip:read", "hip:manage", "hiu:read", "hiu:request"},
    },
}


def permissions_for(user: User) -> set[str]:
    return ROLES.get(user.role.code, {}).get("permissions", set())



# ---------- passwords ----------
# Stored as scrypt$<n>$<r>$<p>$<salt hex>$<digest hex>. The original format scrypt$<salt>$<digest> (n=2**14, r=8,
# p=1) still verifies and is upgraded at the next sign-in (see needs_rehash).
_R, _P = 8, 1
_MAXMEM = 256 * 1024 * 1024  # OpenSSL's 32 MB default is too small from n=2**15


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM)


def hash_password(password: str) -> str:
    salt, n = os.urandom(16), settings.password_scrypt_n
    return f"scrypt${n}${_R}${_P}${salt.hex()}${_scrypt(password, salt, n, _R, _P).hex()}"


def _parse_hash(stored: str) -> tuple[int, int, int, str, str] | None:
    parts = stored.split("$")
    try:
        if len(parts) == 3 and parts[0] == "scrypt":
            return 2**14, 8, 1, parts[1], parts[2]
        if len(parts) == 6 and parts[0] == "scrypt":
            return int(parts[1]), int(parts[2]), int(parts[3]), parts[4], parts[5]
    except ValueError:
        pass
    return None


def verify_password(password: str, stored: str) -> bool:
    parsed = _parse_hash(stored)
    if parsed is None:
        return False
    n, r, p, salt_hex, digest_hex = parsed
    try:
        digest = _scrypt(password, bytes.fromhex(salt_hex), n, r, p)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def needs_rehash(stored: str) -> bool:
    """True when the hash uses older (cheaper) parameters than the current setting."""
    parsed = _parse_hash(stored)
    return parsed is None or parsed[0] < settings.password_scrypt_n or parsed[1:3] != (_R, _P)


# ---------- sessions ----------
# Each sign-in is a UserSession; its token carries the session id ("sid") and the user's token_version. Signing out
# ends one session, revoke_tokens() ends all of them, and a session ends after SESSION_IDLE_MINUTES without use.
# The browser gets the token in an HttpOnly cookie; API clients (and the tests) send it as a Bearer header.
SESSION_COOKIE = "abdm_session"
CSRF_COOKIE = "abdm_csrf"  # readable by the app, which echoes it in X-CSRF-Token (double-submit check)
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOUCH_EVERY = timedelta(seconds=60)  # last_seen_at is written at most this often


def create_session(db: Session, user: User) -> str:
    """Start a session for `user` and return its access token (the caller commits)."""
    now = utcnow()
    session = UserSession(id=secrets.token_urlsafe(32), user_id=user.id, created_at=now, last_seen_at=now,
                          expires_at=now + timedelta(minutes=settings.jwt_expire_minutes))
    db.add(session)
    payload = {"sub": str(user.id), "hid": user.hospital_id, "ver": user.token_version, "sid": session.id,
               "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def set_session_cookies(response: Response, token: str) -> None:
    max_age = settings.jwt_expire_minutes * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, path="/api", httponly=True,
                        secure=settings.cookie_secure, samesite="strict")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=max_age, path="/", httponly=False,
                        secure=settings.cookie_secure, samesite="strict")


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api", secure=settings.cookie_secure, httponly=True,
                           samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="strict")


def revoke_tokens(user: User) -> None:
    """End every session of `user` so far (the caller commits)."""
    user.token_version = (user.token_version or 0) + 1
    db = object_session(user)
    if db is not None and user.id is not None:
        db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))


def end_session(db: Session, session_id: str | None) -> None:
    """End one session (the caller commits)."""
    if session_id:
        db.execute(update(UserSession).where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))


bearer_scheme = HTTPBearer(auto_error=False)


def mfa_setup_required(user: User) -> bool:
    return user.role.code in settings.mfa_required_role_list and not user.mfa_enabled


def get_session_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """The signed-in user, from the Authorization header (API clients) or the session cookie (the browser)."""
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Not authenticated or session expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthorized
    if credentials is None and request.method not in _SAFE_METHODS:
        # Browsers send cookies automatically, so a cookie-authenticated change must prove it comes from the app.
        sent, expected = request.headers.get(CSRF_HEADER, ""), request.cookies.get(CSRF_COOKIE, "")
        if not expected or not hmac.compare_digest(sent, expected):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Request blocked (missing security token). Reload the page.")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                             options={"require": ["exp", "sub", "ver", "sid"]})
        user = db.get(User, int(payload["sub"]))
        version, session_id = int(payload["ver"]), str(payload["sid"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise unauthorized
    if user is None or not user.is_active or not user.hospital.is_active or version != user.token_version:
        raise unauthorized
    session = db.get(UserSession, session_id)
    now = utcnow()
    if (session is None or session.user_id != user.id or session.revoked_at is not None or session.expires_at <= now
            or session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now):
        raise unauthorized
    if now - session.last_seen_at >= _TOUCH_EVERY:
        session.last_seen_at = now
        db.commit()
    request.state.session_id = session_id
    return user


def get_current_user(user: User = Depends(get_session_user)) -> User:
    """The signed-in user, who must also have set up two-step sign-in if their role requires it."""
    if mfa_setup_required(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set up two-step sign-in before using the system")
    return user


def require(permission: str):
    """Dependency that returns the current user if their role grants `permission`."""
    assert permission in ALL_PERMISSIONS, permission

    def checker(user: User = Depends(get_current_user)) -> User:
        if permission not in permissions_for(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to perform this action")
        return user

    return checker

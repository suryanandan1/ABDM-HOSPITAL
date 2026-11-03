"""Password hashing, JWT tokens, roles/permissions and the auth dependencies."""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

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


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_access_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user.id), "hid": user.hospital_id, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Not authenticated or session expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or not user.is_active or not user.hospital.is_active:
        raise unauthorized
    return user


def require(permission: str):
    """Dependency that returns the current user if their role grants `permission`."""
    assert permission in ALL_PERMISSIONS, permission

    def checker(user: User = Depends(get_current_user)) -> User:
        if permission not in permissions_for(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to perform this action")
        return user

    return checker

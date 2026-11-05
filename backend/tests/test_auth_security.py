"""Sign-in security: cookie sessions + CSRF, idle timeout, throttling, password hash upgrades, two-step sign-in."""

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import mfa
from app.config import Settings, settings
from app.database import SessionLocal
from app.main import app
from app.models import LoginFailure, User, UserSession
from app.routers import auth
from tests.conftest import ADMIN_PASSWORD, login
from tests.test_api import NEW_PATIENT

PASSWORD = "Demo@1234"


def _browser():
    # https so the Secure cookies are sent back, as a real browser on https (or localhost) would
    return TestClient(app, base_url="https://testserver")


# ---------- cookie sessions ----------

def test_browser_session_uses_httponly_cookie_and_csrf(client):
    browser = _browser()
    response = browser.post("/api/auth/login", json={"username": "reception", "password": PASSWORD})
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("abdm_session="))
    assert "HttpOnly" in session_cookie and "Secure" in session_cookie and "SameSite=strict" in session_cookie
    assert browser.get("/api/auth/me").status_code == 200  # the cookie alone authenticates reads

    patient = NEW_PATIENT
    blocked = browser.post("/api/patients", json=patient)  # no CSRF header: a forged cross-site request
    assert blocked.status_code == 403
    csrf = browser.cookies.get("abdm_csrf")
    allowed = browser.post("/api/patients", json=patient, headers={"X-CSRF-Token": csrf})
    assert allowed.status_code in (200, 201), allowed.text
    assert browser.post("/api/patients", json=patient, headers={"X-CSRF-Token": "wrong"}).status_code == 403

    assert browser.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert browser.get("/api/auth/me").status_code == 401


def test_hsts_on_https_only(client, admin):
    assert "strict-transport-security" not in client.get("/api/health").headers
    assert _browser().get("/api/health").headers["strict-transport-security"].startswith("max-age=")


def test_idle_session_expires(client):
    headers = login(client, "reception")
    with SessionLocal() as db:
        session = db.scalars(select(UserSession).order_by(UserSession.created_at.desc())).first()
        session.last_seen_at -= timedelta(minutes=settings.session_idle_minutes + 1)
        db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_me_reports_idle_minutes(client, admin):
    assert client.get("/api/auth/me", headers=admin).json()["session_idle_minutes"] == settings.session_idle_minutes


# ---------- throttling ----------

def test_throttling_is_per_address(client, monkeypatch):
    monkeypatch.setattr(auth, "client_ip", lambda request: "10.0.0.66")  # the attacker's computer
    for _ in range(auth.MAX_PER_ACCOUNT_AND_IP):
        assert client.post("/api/auth/login", json={"username": "dr.mehta", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD}).status_code == 429
    monkeypatch.setattr(auth, "client_ip", lambda request: "10.0.0.5")  # the doctor's own computer
    assert client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD}).status_code == 200


def test_throttling_limits_one_address_across_accounts(client, monkeypatch):
    monkeypatch.setattr(auth, "client_ip", lambda request: "10.0.0.66")
    for i in range(auth.MAX_PER_IP):
        client.post("/api/auth/login", json={"username": f"guess{i}", "password": "wrong"})
    assert client.post("/api/auth/login", json={"username": "reception", "password": PASSWORD}).status_code == 429


def test_throttling_is_stored_in_the_database(client):
    client.post("/api/auth/login", json={"username": "labtech", "password": "wrong"})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LoginFailure).where(LoginFailure.username == "labtech")) == 1
    login(client, "labtech")  # success clears them
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LoginFailure).where(LoginFailure.username == "labtech")) == 0


# ---------- password hashing ----------

def test_legacy_password_hash_is_upgraded_at_sign_in(client, monkeypatch):
    import hashlib
    import os
    salt = os.urandom(16)
    legacy = f"scrypt${salt.hex()}${hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "labtech")).password_hash = legacy
        db.commit()
    monkeypatch.setattr(settings, "password_scrypt_n", 2**15)
    login(client, "labtech")
    with SessionLocal() as db:
        upgraded = db.scalar(select(User).where(User.username == "labtech")).password_hash
    assert upgraded.startswith("scrypt$32768$8$1$")
    login(client, "labtech")  # and still verifies


def test_scrypt_cost_setting_is_checked():
    with pytest.raises(ValueError):
        Settings(jwt_secret="x" * 40, password_scrypt_n=1000)


def test_sandbox_mode_needs_sealing_key():
    with pytest.raises(ValueError, match="RECORD_SEALING_KEY"):
        Settings(jwt_secret="x" * 40, abdm_mode="sandbox")
    Settings(jwt_secret="x" * 40, abdm_mode="sandbox", record_sealing_key="k" * 40)


# ---------- two-step sign-in ----------

def test_totp_matches_rfc6238_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890" (RFC 6238 appendix B)
    assert mfa.code_at(secret, 59) == "287082"
    assert mfa.code_at(secret, 1111111109) == "081804"


def _enable_mfa(client, headers) -> tuple[str, dict]:
    setup = client.post("/api/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/auth/mfa/enable", headers=headers, json={"code": "000000"}).status_code == 400
    enabled = client.post("/api/auth/mfa/enable", headers=headers, json={"code": mfa.code_at(secret, time.time())})
    assert enabled.status_code == 200, enabled.text
    client.cookies.clear()
    return secret, {"Authorization": f"Bearer {enabled.json()['access_token']}"}


def test_mfa_sign_in(client, admin):
    headers = login(client, "dr.mehta")
    secret, fresh = _enable_mfa(client, headers)
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # sessions before enabling end
    assert client.get("/api/auth/me", headers=fresh).json()["mfa_enabled"] is True
    with SessionLocal() as db:
        assert secret not in db.scalar(select(User).where(User.username == "dr.mehta")).mfa_secret  # sealed

    needs_code = client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD})
    assert needs_code.status_code == 401 and needs_code.json()["detail"]["code"] == "mfa_required"
    wrong = client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD, "otp": "123456"})
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "mfa_invalid"
    code = mfa.code_at(secret, time.time() + mfa.STEP_SECONDS)  # the next step (the current one was used to enable)
    ok = client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD, "otp": code})
    assert ok.status_code == 200
    replay = client.post("/api/auth/login", json={"username": "dr.mehta", "password": PASSWORD, "otp": code})
    assert replay.status_code == 401  # a code works once

    # An administrator can reset it (lost phone); the doctor then signs in with the password alone.
    doctor_id = client.get("/api/auth/me", headers=fresh).json()["id"]
    reset = client.delete(f"/api/staff/{doctor_id}/mfa", headers=admin)
    assert reset.status_code == 200 and reset.json()["mfa_enabled"] is False
    login(client, "dr.mehta")


def test_mfa_disable_needs_password_and_code(client):
    secret, headers = _enable_mfa(client, login(client, "reception"))
    bad = client.post("/api/auth/mfa/disable", headers=headers, json={"password": "wrong", "code": "000000"})
    assert bad.status_code == 400
    code = mfa.code_at(secret, time.time() + mfa.STEP_SECONDS)
    assert client.post("/api/auth/mfa/disable", headers=headers,
                       json={"password": PASSWORD, "code": code}).status_code == 204
    login(client, "reception")


def test_required_mfa_blocks_everything_until_set_up(client, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_roles", "hospital_admin")
    headers = login(client, "admin", ADMIN_PASSWORD)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["mfa_setup_required"] is True
    assert client.get("/api/staff", headers=headers).status_code == 403
    secret, fresh = _enable_mfa(client, headers)
    assert client.get("/api/staff", headers=fresh).status_code == 200
    code = mfa.code_at(secret, time.time() + mfa.STEP_SECONDS)
    refused = client.post("/api/auth/mfa/disable", headers=fresh, json={"password": ADMIN_PASSWORD, "code": code})
    assert refused.status_code == 400  # a required role cannot turn it off
    assert login(client, "reception")  # other roles are unaffected

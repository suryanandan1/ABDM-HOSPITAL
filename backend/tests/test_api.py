from tests.conftest import login

NEW_PATIENT = {
    "full_name": "Meera Nair", "gender": "F", "mobile": "+91 98450 12345", "date_of_birth": "1990-06-15",
    "city": "Kochi", "state": "Kerala", "pincode": "682001",
    "abha_number": "91-1234-5678-9012", "abha_address": "Meera.Nair@abdm",
    "emergency_contact_name": "Ravi Nair", "emergency_contact_phone": "9845099999",
    "emergency_contact_relation": "Spouse",
}


def register(client, headers, **overrides):
    return client.post("/api/patients", json={**NEW_PATIENT, **overrides}, headers=headers)


# ---------- auth ----------

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_login_and_me(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    assert me["username"] == "admin"
    assert me["role_code"] == "hospital_admin"
    assert "staff:manage" in me["permissions"]
    assert me["hospital_name"] == "Test Hospital"


def test_login_failure_is_rejected_and_audited(client, admin):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    logs = client.get("/api/audit-logs?action=auth.login_failed", headers=admin).json()
    assert logs["total"] == 1


def test_requests_without_valid_token_are_rejected(client):
    assert client.get("/api/patients").status_code == 401
    assert client.get("/api/patients", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_change_password(client):
    headers = login(client, "reception")
    bad = client.post("/api/auth/change-password", headers=headers,
                      json={"current_password": "nope", "new_password": "NewPass123"})
    assert bad.status_code == 400
    weak = client.post("/api/auth/change-password", headers=headers,
                       json={"current_password": "Demo@1234", "new_password": "short"})
    assert weak.status_code == 422
    other_session = login(client, "reception")
    ok = client.post("/api/auth/change-password", headers=headers,
                     json={"current_password": "Demo@1234", "new_password": "NewPass123"})
    assert ok.status_code == 200
    # Every earlier session ends; the session that changed the password continues with its new token.
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=other_session).status_code == 401
    fresh = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200
    login(client, "reception", "NewPass123")


def test_logout_ends_only_this_session(client):
    first, second = login(client, "reception"), login(client, "reception")
    assert client.post("/api/auth/logout", headers=first).status_code == 204
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers=second).status_code == 200  # the other computer stays signed in


def test_logout_all_ends_every_session(client):
    first, second = login(client, "reception"), login(client, "reception")
    assert client.post("/api/auth/logout-all", headers=first).status_code == 204
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers=second).status_code == 401
    assert client.get("/api/auth/me", headers=login(client, "reception")).status_code == 200


def test_staff_changes_revoke_sessions(client, admin):
    staff = {u["username"]: u["id"] for u in client.get("/api/staff", headers=admin).json()["items"]}
    reception, labtech = login(client, "reception"), login(client, "labtech")
    # Editing contact details keeps sessions; a password reset or deactivation ends them.
    assert client.patch(f"/api/staff/{staff['reception']}", headers=admin, json={"phone": "9876543210"}).status_code == 200
    assert client.get("/api/auth/me", headers=reception).status_code == 200
    assert client.patch(f"/api/staff/{staff['reception']}", headers=admin, json={"password": "Reset1234"}).status_code == 200
    assert client.get("/api/auth/me", headers=reception).status_code == 401
    assert client.patch(f"/api/staff/{staff['labtech']}", headers=admin, json={"is_active": False}).status_code == 200
    client.patch(f"/api/staff/{staff['labtech']}", headers=admin, json={"is_active": True})
    assert client.get("/api/auth/me", headers=labtech).status_code == 401  # reactivation does not revive old tokens


def test_token_without_version_is_rejected(client):
    import jwt
    from app.config import settings
    token = jwt.encode({"sub": "1", "hid": 1, "exp": 9999999999}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_api_csp_header(client, admin):
    csp = client.get("/api/auth/me", headers=admin).headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp


# ---------- role permissions ----------

def test_role_permissions(client):
    doctor, reception, lab, hims = (login(client, u) for u in ("dr.mehta", "reception", "labtech", "hims"))
    assert register(client, doctor).status_code == 403
    assert register(client, lab).status_code == 403
    assert client.get("/api/patients", headers=lab).status_code == 200
    assert client.get("/api/staff", headers=reception).status_code == 403
    assert client.get("/api/audit-logs", headers=reception).status_code == 403
    assert client.get("/api/audit-logs", headers=hims).status_code == 200
    assert client.put("/api/hospital", json={"name": "X Hospital"}, headers=hims).status_code == 403
    assert client.get("/api/doctors", headers=lab).status_code == 403
    assert client.get("/api/dashboard", headers=lab).json()["recent_activity"] == []


# ---------- hospital ----------

def test_hospital_profile(client, admin):
    assert client.get("/api/hospital", headers=admin).json()["code"] == "TST"
    updated = client.put("/api/hospital", headers=admin,
                         json={"hfr_id": "IN2710000123", "phone": "022-12345678", "pincode": "400001"})
    assert updated.status_code == 200
    assert updated.json()["hfr_id"] == "IN2710000123"
    assert client.put("/api/hospital", headers=admin, json={"pincode": "12"}).status_code == 422
    assert client.put("/api/hospital", headers=admin, json={"name": ""}).status_code == 422


# ---------- staff ----------

def test_staff_crud(client, admin):
    roles = client.get("/api/roles", headers=admin).json()
    assert {r["code"] for r in roles} == {"hospital_admin", "doctor", "receptionist", "lab_technician",
                                          "health_info_staff"}

    body = {"username": "Nurse.Joy", "password": "Secret123", "full_name": "Joy Thomas", "role": "receptionist"}
    created = client.post("/api/staff", json=body, headers=admin)
    assert created.status_code == 201
    staff_id = created.json()["id"]
    assert created.json()["username"] == "nurse.joy"
    assert client.post("/api/staff", json=body, headers=admin).status_code == 409
    assert client.post("/api/staff", json={**body, "username": "x", "role": "boss"}, headers=admin).status_code == 422

    listing = client.get("/api/staff?q=joy", headers=admin).json()
    assert listing["total"] == 1

    updated = client.patch(f"/api/staff/{staff_id}", json={"role": "lab_technician", "is_active": False},
                           headers=admin)
    assert updated.json()["role_code"] == "lab_technician"
    assert updated.json()["is_active"] is False
    login_disabled = client.post("/api/auth/login", json={"username": "nurse.joy", "password": "Secret123"})
    assert login_disabled.status_code == 403

    me = client.get("/api/auth/me", headers=admin).json()
    assert client.patch(f"/api/staff/{me['id']}", json={"is_active": False}, headers=admin).status_code == 400
    assert client.patch(f"/api/staff/{me['id']}", json={"role": "doctor"}, headers=admin).status_code == 400
    assert client.get("/api/staff/9999", headers=admin).status_code == 404


def test_password_reset_by_admin(client, admin):
    staff = client.get("/api/staff?q=labtech", headers=admin).json()["items"][0]
    assert client.patch(f"/api/staff/{staff['id']}", json={"password": "Reset1234"}, headers=admin).status_code == 200
    login(client, "labtech", "Reset1234")


# ---------- doctors ----------

def test_doctor_crud(client, admin):
    doctors = client.get("/api/doctors", headers=admin).json()
    assert doctors["total"] == 2
    body = {"full_name": "Dr. Farhan Ali", "registration_number": "MMC-11223", "specialization": "Cardiology"}
    created = client.post("/api/doctors", json=body, headers=admin)
    assert created.status_code == 201
    assert client.post("/api/doctors", json=body, headers=admin).status_code == 409

    # Linking requires a free account with the Doctor role.
    reception_id = client.get("/api/staff?q=reception", headers=admin).json()["items"][0]["id"]
    doctor_id = created.json()["id"]
    assert client.patch(f"/api/doctors/{doctor_id}", json={"user_id": reception_id}, headers=admin).status_code == 400
    mehta_id = client.get("/api/staff?q=mehta", headers=admin).json()["items"][0]["id"]
    assert client.patch(f"/api/doctors/{doctor_id}", json={"user_id": mehta_id}, headers=admin).status_code == 409

    updated = client.patch(f"/api/doctors/{doctor_id}", json={"is_active": False, "department": "Cardiac"},
                           headers=admin)
    assert updated.json()["is_active"] is False
    assert client.get("/api/doctors?is_active=true", headers=admin).json()["total"] == 2
    assert client.get("/api/doctors?q=cardio", headers=admin).json()["total"] == 1


# ---------- patients ----------

def test_register_patient_normalises_fields_and_is_unverified(client):
    reception = login(client, "reception")
    response = register(client, reception)
    assert response.status_code == 201, response.text
    patient = response.json()
    assert patient["patient_code"].startswith("TST-")
    assert patient["mobile"] == "9845012345"
    assert patient["abha_number"] == "91123456789012"
    assert patient["abha_address"] == "meera.nair@abdm"
    assert patient["abha_verified"] is False
    assert patient["year_of_birth"] == 1990
    assert patient["registered_by_name"] == "Priya Sharma"


def test_patient_validation(client):
    reception = login(client, "reception")
    assert register(client, reception, mobile="12345").status_code == 422
    assert register(client, reception, abha_number="1234").status_code == 422
    assert register(client, reception, abha_address="bad address").status_code == 422
    assert register(client, reception, date_of_birth="2999-01-01").status_code == 422
    assert register(client, reception, date_of_birth=None).status_code == 422  # neither DOB nor age
    with_age = register(client, reception, date_of_birth=None, age=40, abha_number=None, abha_address=None)
    assert with_age.status_code == 201
    assert with_age.json()["date_of_birth"] is None


def test_duplicate_workflow(client):
    reception = login(client, "reception")
    assert register(client, reception).status_code == 201

    # Same ABHA is never allowed twice.
    same_abha = register(client, reception, full_name="Someone Else", mobile="9000000001",
                         abha_address=None, confirm_not_duplicate=True)
    assert same_abha.status_code == 409
    assert same_abha.json()["detail"]["duplicates"][0]["level"] == "exact"

    # Same person without ABHA: warned, then allowed only with explicit confirmation.
    probable = {"full_name": "Meera  Nair", "abha_number": None, "abha_address": None}
    check = client.post("/api/patients/duplicate-check", json={**NEW_PATIENT, **probable}, headers=reception)
    assert check.json()[0]["level"] == "likely"
    blocked = register(client, reception, **probable)
    assert blocked.status_code == 409
    assert "Same mobile number" in blocked.json()["detail"]["duplicates"][0]["reasons"]
    assert register(client, reception, **probable, confirm_not_duplicate=True).status_code == 201

    # Name order swapped and similar birth year with a different phone: possible duplicate.
    swapped = client.post("/api/patients/duplicate-check", headers=reception, json={
        **NEW_PATIENT, "full_name": "Nair Meera", "mobile": "9111111111", "date_of_birth": "1991-01-01",
        "abha_number": None, "abha_address": None})
    assert swapped.json()[0]["level"] == "possible"

    # A family member sharing the phone is not flagged.
    family = client.post("/api/patients/duplicate-check", headers=reception, json={
        **NEW_PATIENT, "full_name": "Ravi Nair", "gender": "M", "abha_number": None, "abha_address": None})
    assert family.json() == []


def test_search_uses_only_verified_abha(client):
    reception, hims = login(client, "reception"), login(client, "hims")
    patient = register(client, reception).json()

    def search(term):
        return client.get("/api/patients", params={"q": term}, headers=reception).json()["total"]

    assert search("meera") == 1
    assert search(patient["patient_code"]) == 1
    assert search("98450 12345") == 1
    assert search("91-1234-5678-9012") == 0
    assert search("meera.nair@abdm") == 0

    # Receptionists cannot verify; authorised health information staff can.
    verify = {"method": "physical_abha_card", "note": "ABHA card checked at desk"}
    assert client.post(f"/api/patients/{patient['id']}/abha/verify", json=verify, headers=reception).status_code == 403
    verified = client.post(f"/api/patients/{patient['id']}/abha/verify", json=verify, headers=hims)
    assert verified.status_code == 200
    assert verified.json()["abha_verified"] is True
    assert verified.json()["abha_verified_by_name"] == "Kavita Iyer"

    assert search("91-1234-5678-9012") == 1
    assert search("MEERA.NAIR@abdm") == 1
    assert client.get("/api/patients?abha_verified=true", headers=reception).json()["total"] == 1


def test_changing_abha_resets_verification(client):
    reception, hims = login(client, "reception"), login(client, "hims")
    patient = register(client, reception).json()
    client.post(f"/api/patients/{patient['id']}/abha/verify",
                json={"method": "abha_app_profile", "note": "Shown in ABHA app"}, headers=hims)
    updated = client.patch(f"/api/patients/{patient['id']}", json={"abha_number": "91-9999-8888-7777"},
                           headers=reception)
    assert updated.status_code == 200
    assert updated.json()["abha_verified"] is False
    assert updated.json()["abha_verification_method"] is None

    # Updating other fields keeps the record intact.
    renamed = client.patch(f"/api/patients/{patient['id']}", json={"city": "Thrissur", "age": 36}, headers=reception)
    assert renamed.json()["city"] == "Thrissur"
    assert renamed.json()["date_of_birth"] is None
    assert client.patch(f"/api/patients/{patient['id']}", json={"full_name": ""}, headers=reception).status_code == 422


def test_abha_must_stay_unique_on_update(client):
    reception = login(client, "reception")
    register(client, reception)
    other = client.get("/api/patients?q=ramesh", headers=reception).json()["items"][0]
    clash = client.patch(f"/api/patients/{other['id']}", json={"abha_address": "meera.nair@abdm"}, headers=reception)
    assert clash.status_code == 409


def test_revoke_verification(client):
    hims = login(client, "hims")
    patient_id = register(client, login(client, "reception")).json()["id"]
    assert client.delete(f"/api/patients/{patient_id}/abha/verify", headers=hims).status_code == 400
    client.post(f"/api/patients/{patient_id}/abha/verify",
                json={"method": "physical_abha_card", "note": "Card checked"}, headers=hims)
    revoked = client.delete(f"/api/patients/{patient_id}/abha/verify", headers=hims)
    assert revoked.json()["abha_verified"] is False


def test_verify_needs_an_identifier(client):
    hims = login(client, "hims")
    ramesh = client.get("/api/patients?q=ramesh", headers=hims).json()["items"][0]
    response = client.post(f"/api/patients/{ramesh['id']}/abha/verify",
                           json={"method": "physical_abha_card", "note": "Card checked"}, headers=hims)
    assert response.status_code == 400


def test_pagination(client):
    reception = login(client, "reception")
    page = client.get("/api/patients?page=1&page_size=2", headers=reception).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    page2 = client.get("/api/patients?page=2&page_size=2", headers=reception).json()
    assert len(page2["items"]) == 1
    assert client.get("/api/patients?page=0", headers=reception).status_code == 422


def test_patient_detail_is_audited(client, admin):
    doctor = login(client, "dr.mehta")
    patient_id = client.get("/api/patients", headers=doctor).json()["items"][0]["id"]
    assert client.get(f"/api/patients/{patient_id}", headers=doctor).status_code == 200
    assert client.get("/api/patients/9999", headers=doctor).status_code == 404
    logs = client.get(f"/api/audit-logs?action=patient.view&entity_id={patient_id}", headers=admin).json()
    assert logs["total"] == 1
    assert logs["items"][0]["user_name"] == "Dr. Anil Mehta"


# ---------- dashboard ----------

def test_dashboard(client, admin):
    register(client, admin)
    data = client.get("/api/dashboard", headers=admin).json()
    assert data["stats"] == {"total_patients": 4, "patients_today": 4, "abha_verified_patients": 0,
                             "active_doctors": 2, "active_staff": 6, "consultations_today": 0,
                             "lab_orders_open": 0, "lab_reports_awaiting_approval": 0}
    assert data["recent_patients"][0]["full_name"] == "Meera Nair"
    assert data["recent_activity"][0]["action"] == "patient.create"


# ---------- multi-hospital isolation ----------

def test_hospitals_are_isolated(client):
    from app.database import SessionLocal
    from init_db import seed

    with SessionLocal() as db:
        seed(db, "Other Hospital", "OTH", "other.admin", "Other1234")
    other = login(client, "other.admin", "Other1234")
    assert client.get("/api/patients", headers=other).json()["total"] == 0
    tst_patient = client.get("/api/patients", headers=login(client, "reception")).json()["items"][0]
    assert client.get(f"/api/patients/{tst_patient['id']}", headers=other).status_code == 404
    assert client.get("/api/hospital", headers=other).json()["code"] == "OTH"

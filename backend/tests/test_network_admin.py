"""Network administrator: one account that manages every hospital and its staff, with no patient access; each
hospital's own administrator stays limited to that hospital."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AuditLog, Hospital, Role, User
from app.security import hash_password
from init_db import seed_network
from tests.conftest import login

PASSWORD = "Network1234"


@pytest.fixture()
def network(client, monkeypatch):
    monkeypatch.setattr(settings, "abdm_mode", "simulation")
    with SessionLocal() as db:
        seed_network(db, db.scalar(select(Hospital).where(Hospital.code == "TST")))
        role = db.scalar(select(Role).where(Role.code == "network_admin"))
        db.add(User(hospital_id=None, role=role, username="superadmin", password_hash=hash_password(PASSWORD),
                    full_name="Network Administrator"))
        db.commit()
    return client, login(client, "superadmin", PASSWORD)


def test_network_admin_sees_every_hospital_but_no_patients(network):
    client, root = network
    me = client.get("/api/auth/me", headers=root).json()
    assert me["hospital_id"] is None and me["hospital_name"] is None and me["permissions"] == ["network:manage"]
    hospitals = client.get("/api/network/hospitals", headers=root).json()
    assert [h["code"] for h in hospitals] == ["TST", "GVH", "RMC"]
    gvh = hospitals[1]
    assert [a["username"] for a in gvh["admins"]] == ["gvh.admin"] and gvh["stats"]["patients"] >= 4
    for path in ("/api/patients", "/api/consultations", "/api/hip/consents", "/api/hiu/requests", "/api/staff",
                 "/api/hospital", "/api/audit-logs", "/api/dashboard"):
        assert client.get(path, headers=root).status_code == 403, path


def test_hospital_admins_cannot_use_network_administration(network):
    client, _ = network
    for username in ("gvh.admin", "rmc.admin"):
        headers = login(client, username)
        assert client.get("/api/network/hospitals", headers=headers).status_code == 403
        assert "network:manage" not in client.get("/api/auth/me", headers=headers).json()["permissions"]
        roles = [r["code"] for r in client.get("/api/roles", headers=headers).json()]
        assert "network_admin" not in roles
        bad = client.post("/api/staff", headers=headers, json={"username": "sneaky", "password": "Sneaky1234",
                                                               "full_name": "Sneaky", "role": "network_admin"})
        assert bad.status_code == 422
    # the network administrator is nobody's staff member
    staff = client.get("/api/staff", headers=login(client, "gvh.admin"), params={"q": "superadmin"}).json()
    assert staff["items"] == []


def test_add_hospital_with_its_own_admin(network):
    client, root = network
    created = client.post("/api/network/hospitals", headers=root, json={
        "name": "Lakeside Hospital", "code": "lkh", "city": "Pune", "state": "Maharashtra",
        "admin": {"username": "lkh.admin", "full_name": "Lakeside Admin", "password": "Lakeside1234"}})
    assert created.status_code == 201, created.text
    hospital = created.json()
    assert hospital["code"] == "LKH" and hospital["abdm_hip_id"] == "SIM_HIP_LKH" and hospital["abdm_hiu_id"] == "SIM_HIU_LKH"
    assert [a["username"] for a in hospital["admins"]] == ["lkh.admin"]
    # the new hospital's admin runs only their own hospital
    lkh = login(client, "lkh.admin", "Lakeside1234")
    me = client.get("/api/auth/me", headers=lkh).json()
    assert me["hospital_name"] == "Lakeside Hospital" and me["role_code"] == "hospital_admin"
    assert client.get("/api/patients", headers=lkh).json()["items"] == []
    status = client.get("/api/hip/status", headers=lkh).json()
    assert status["hip_id"] == "SIM_HIP_LKH" and status["hiu_id"] == "SIM_HIU_LKH"
    # duplicates are refused
    again = client.post("/api/network/hospitals", headers=root, json={"name": "Other", "code": "LKH"})
    assert again.status_code == 409
    clash = client.post("/api/network/hospitals", headers=root, json={"name": "Other", "code": "OTH",
                                                                      "abdm_hip_id": "SIM_HIP_GVH"})
    assert clash.status_code == 409


def test_manage_staff_of_any_hospital_and_audit_it(network):
    client, root = network
    hospitals = {h["code"]: h for h in client.get("/api/network/hospitals", headers=root).json()}
    rmc = hospitals["RMC"]["id"]
    added = client.post(f"/api/network/hospitals/{rmc}/staff", headers=root, json={
        "username": "rmc.nurse", "password": "Nurse12345", "full_name": "Asha Nurse", "role": "receptionist"})
    assert added.status_code == 201 and added.json()["hospital_id"] == rmc
    staff = client.get(f"/api/network/hospitals/{rmc}/staff", headers=root, params={"q": "rmc.admin"}).json()["items"][0]
    reset = client.patch(f"/api/network/staff/{staff['id']}", headers=root, json={"password": "NewAdmin1234"})
    assert reset.status_code == 200
    login(client, "rmc.admin", "NewAdmin1234")
    off = client.patch(f"/api/network/staff/{staff['id']}", headers=root, json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert client.post("/api/auth/login", json={"username": "rmc.admin", "password": "NewAdmin1234"}).status_code == 403
    # the hospital's own audit log shows what the network administrator did
    with SessionLocal() as db:
        actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.hospital_id == rmc))
                   if a.user and a.user.username == "superadmin"}
    assert {"staff.create", "staff.update"} <= actions
    # network administrators themselves are not managed through the hospital staff API
    root_id = client.get("/api/auth/me", headers=root).json()["id"]
    assert client.patch(f"/api/network/staff/{root_id}", headers=root, json={"is_active": False}).status_code == 404


def test_deactivating_a_hospital_signs_its_staff_out_and_stops_abdm_routing(network):
    client, root = network
    gvh = next(h for h in client.get("/api/network/hospitals", headers=root).json() if h["code"] == "GVH")
    staff = login(client, "gvh.hims")
    off = client.patch(f"/api/network/hospitals/{gvh['id']}", headers=root, json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert client.get("/api/patients", headers=staff).status_code == 401
    assert client.post("/api/auth/login", json={"username": "gvh.hims", "password": "Demo@1234"}).status_code == 403
    codes = [h["code"] for h in client.get("/api/hip/network", headers=login(client, "hims")).json()["hospitals"]]
    assert "GVH" not in codes
    on = client.patch(f"/api/network/hospitals/{gvh['id']}", headers=root, json={"is_active": True, "city": "Mysuru"})
    assert on.json()["is_active"] is True and on.json()["city"] == "Mysuru"
    login(client, "gvh.hims")

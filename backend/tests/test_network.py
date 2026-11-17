"""Several hospitals on one bridge (init_db.seed_network): routing by HIP/HIU ID and record exchange between them.

The demo patient Surya Prakash has one ABHA address, surya@abdm, at every hospital. A record Green Valley links to
it is requested by the primary hospital (as HIU), consented to in the simulated ABHA app and sent by Green Valley's
HIP code, encrypted, to the primary hospital's push URL.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.hip import gateway, simulator
from app.models import FhirRecord, Hospital
from init_db import seed_network
from tests.conftest import login
from tests.test_clinical import MEDICINES, start_consultation

TODAY = date.today().isoformat()


@pytest.fixture()
def network(client, monkeypatch):
    monkeypatch.setattr(settings, "abdm_mode", "simulation")
    monkeypatch.setattr(settings, "hiu_auto_validate", False)
    simulator.reset()
    gateway.reset()
    with SessionLocal() as db:
        seed_network(db, db.scalar(select(Hospital).where(Hospital.code == "TST")))
    yield client
    simulator.reset()


def patient(client, headers, address):
    return client.get("/api/patients", params={"q": address}, headers=headers).json()["items"][0] if address else None


def shareable_record_at_green_valley(client):
    """An approved, validated prescription for surya@abdm at Green Valley, verified and linked through ABDM."""
    doctor, hims = login(client, "gvh.shetty"), login(client, "gvh.hims")
    surya = client.get("/api/patients", params={"q": "Surya"}, headers=hims).json()["items"][0]
    assert surya["abha_address"] == "surya@abdm" and surya["patient_code"].startswith("GVH-")
    consult = start_consultation(client, doctor, surya["id"])
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    client.post(f"/api/prescriptions/{rx['id']}/submit", headers=doctor)
    client.post(f"/api/prescriptions/{rx['id']}/approve", headers=doctor)
    record = client.post("/api/fhir/records", headers=hims, json={"source_type": "prescription", "source_id": rx["id"]}).json()
    with SessionLocal() as db:  # HL7 validation itself is covered by the Phase 3 tests
        db.get(FhirRecord, record["id"]).validation_status = "valid"
        db.commit()
    verify_and_link(client, hims, surya)
    return surya


def verify_and_link(client, hims, p):
    """Verify the ABHA address with simulated ABDM (the demo accounts exist already) and link all care contexts."""
    assert client.post(f"/api/hip/patients/{p['id']}/link-token", headers=hims).status_code == 200
    overview = client.get(f"/api/hip/patients/{p['id']}", headers=hims).json()
    assert overview["link"]["status"] == "verified", overview["link"]
    ids = [c["id"] for c in overview["care_contexts"]]
    if ids:
        client.post(f"/api/hip/patients/{p['id']}/care-contexts/link", headers=hims, json={"care_context_ids": ids})
    return client.get(f"/api/hip/patients/{p['id']}", headers=hims).json()


def test_each_hospital_has_its_own_abdm_identity(network):
    client = network
    staff = {"hims": "TST", "gvh.hims": "GVH", "rmc.hims": "RMC"}
    for username, code in staff.items():
        status = client.get("/api/hip/status", headers=login(client, username)).json()
        assert status["hip_id"] == f"SIM_HIP_{code}" and status["hiu_id"] == f"SIM_HIU_{code}" and status["ready"]
    hospitals = client.get("/api/hip/network", headers=login(client, "gvh.hims")).json()["hospitals"]
    assert [(h["code"], h["hip_id"], h["this_hospital"]) for h in hospitals] == [
        ("TST", "SIM_HIP_TST", False), ("GVH", "SIM_HIP_GVH", True), ("RMC", "SIM_HIP_RMC", False)]
    # one ABHA address per person, the same at each hospital where they are registered
    for username in staff:
        assert patient(client, login(client, username), "Surya")["abha_address"] == "surya@abdm"
    # IDs are unique across hospitals
    clash = client.put("/api/hospital", headers=login(client, "gvh.admin"), json={"abdm_hip_id": "SIM_HIP_RMC"})
    assert clash.status_code == 409


def test_callbacks_are_routed_by_facility_id(network):
    client = network
    body = {"transactionId": "t-1", "patient": {"id": "surya@abdm", "name": "Surya Prakash", "gender": "M", "yearOfBirth": 1990}}
    url = "/api/v3/hip/patient/care-context/discover"

    def headers(hip_id):
        return {"Authorization": f"Bearer {simulator.issue_token()}", "X-HIP-ID": hip_id, "REQUEST-ID": "r-1"}

    assert client.post(url, json=body, headers=headers("SIM_HIP_UNKNOWN")).status_code == 401
    assert client.post(url, json=body, headers=headers("SIM_HIP_001")).status_code == 401  # the default is no one's now
    assert client.post(url, json=body, headers=headers("SIM_HIP_RMC")).status_code == 202
    # processed by Riverside only: it matched its own patient record
    rmc = client.get("/api/hip/transactions", params={"kind": "discover"}, headers=login(client, "rmc.hims")).json()["items"]
    gvh = client.get("/api/hip/transactions", params={"kind": "discover"}, headers=login(client, "gvh.hims")).json()["items"]
    assert len(rmc) == 1 and rmc[0]["patient"]["patient_code"].startswith("RMC-") and gvh == []


def test_records_flow_from_one_hospital_to_another(network):
    client = network
    shareable_record_at_green_valley(client)
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    surya = patient(client, hims, "Surya")
    verify_and_link(client, hims, surya)  # the requesting hospital needs a verified ABHA address
    request = client.post("/api/hiu/requests", headers=doctor, json={
        "patient_id": surya["id"], "purpose_code": "CAREMGT", "hi_types": ["Prescription", "DiagnosticReport"],
        "date_from": "2024-01-01", "date_to": TODAY}).json()
    assert request["status"] == "pending_consent"
    init = simulator.last_call(gateway.CONSENT_INIT_PATH)["body"]["consent"]
    assert init["hiu"] == {"id": "SIM_HIU_TST"} and init["patient"] == {"id": "surya@abdm"}

    granted = client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True}).json()
    assert granted["status"] == "received", granted
    detail = client.get(f"/api/hiu/requests/{request['id']}", headers=doctor).json()
    assert [(a["hip_id"], a["hip_name"], a["status"]) for a in detail["artefacts"]] == [
        ("SIM_HIP_GVH", "Green Valley Multispeciality Hospital", "valid")]
    records = client.get(f"/api/hiu/patients/{surya['id']}/records", headers=doctor).json()["records"]
    assert [(r["source_hip_id"], r["source_name"], r["category"], r["checksum_ok"]) for r in records] == [
        ("SIM_HIP_GVH", "Green Valley Multispeciality Hospital", "Prescription", True)]

    # Green Valley's HIP side: it checked the consent and sent the record to the primary hospital
    gvh = login(client, "gvh.hims")
    consent = client.get("/api/hip/consents", headers=gvh).json()["items"][0]
    assert consent["status"] == "GRANTED" and consent["abha_address"] == "surya@abdm"
    assert consent["hiu_id"] == "SIM_HIU_TST" and consent["hiu_name"] == "Test Hospital"
    transfer = client.get("/api/hip/transactions", params={"kind": "health_information"}, headers=gvh).json()["items"][0]
    assert transfer["status"] == "success" and "sent to SIM_HIU_TST" in transfer["message"]
    assert client.get("/api/hip/consents", headers=login(client, "rmc.hims")).json()["items"] == []  # held nothing

    # revoking reaches the HIP too, and the received copy is erased
    revoked = client.post(f"/api/hiu/simulate/requests/{request['id']}/revoke", headers=doctor).json()
    assert revoked["status"] == "revoked"
    assert client.get("/api/hip/consents", headers=gvh).json()["items"][0]["status"] == "REVOKED"
    assert client.get(f"/api/hiu/patients/{surya['id']}/records", headers=doctor).json()["records"] == []


def test_grant_needs_records_at_another_hospital(network):
    client = network
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    arjun = patient(client, hims, "Arjun")  # registered only at the primary hospital
    verify_and_link(client, hims, arjun)
    request = client.post("/api/hiu/requests", headers=doctor, json={
        "patient_id": arjun["id"], "purpose_code": "CAREMGT", "hi_types": ["Prescription"],
        "date_from": "2024-01-01", "date_to": TODAY}).json()
    refused = client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True})
    assert refused.status_code == 409 and "arjun.singh@abdm" in refused.json()["detail"]

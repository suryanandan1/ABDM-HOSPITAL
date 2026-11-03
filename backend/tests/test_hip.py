"""Phase 4: ABDM HIP. Simulated workflows, encryption vectors, sandbox contract tests and an optional live check."""

import base64
import json
import os
import re

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.config import settings
from app.database import SessionLocal
from app.hip import crypto, gateway, simulator
from app.models import FhirRecord, HipTransaction, Patient
from tests.conftest import login
from tests.test_clinical import MEDICINES, start_consultation

CALLBACK = "/api/v3"


@pytest.fixture(autouse=True)
def clean_hip(monkeypatch):
    monkeypatch.setattr(settings, "abdm_mode", "simulation")
    simulator.reset()
    gateway.reset()
    yield
    simulator.reset()
    gateway.reset()


def shareable_prescription(client, abha_address=None):
    """Ramesh Kumar with an approved prescription whose FHIR record is HIP-eligible (validated)."""
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    patient = client.get("/api/patients?q=ramesh", headers=hims).json()["items"][0]
    if abha_address:
        client.patch(f"/api/patients/{patient['id']}", headers=login(client, "reception"), json={"abha_address": abha_address})
    consult = start_consultation(client, doctor, patient["id"])
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    client.post(f"/api/prescriptions/{rx['id']}/submit", headers=doctor)
    client.post(f"/api/prescriptions/{rx['id']}/approve", headers=doctor)
    record = client.post("/api/fhir/records", headers=hims, json={"source_type": "prescription", "source_id": rx["id"]}).json()
    with SessionLocal() as db:  # HL7 validation itself is covered by the Phase 3 tests
        db.get(FhirRecord, record["id"]).validation_status = "valid"
        db.commit()
    return patient, record, hims


def register_abha(client, hims, patient, address, **changes):
    account = {"abha_address": address, "name": patient["full_name"], "gender": patient["gender"],
               "year_of_birth": patient["year_of_birth"], "abha_number": "91123456789012", **changes}
    assert client.post("/api/hip/simulate/abha", headers=hims, json=account).status_code == 200


def signed(**extra):
    return {"Authorization": f"Bearer {simulator.issue_token()}", "X-HIP-ID": gateway.hip_id(),
            "REQUEST-ID": "11111111-2222-3333-4444-555555555555", "TIMESTAMP": gateway.timestamp(), **extra}


def link_via_token(client, hims, patient):
    client.post(f"/api/hip/patients/{patient['id']}/link-token", headers=hims)
    overview = client.get(f"/api/hip/patients/{patient['id']}", headers=hims).json()
    ids = [c["id"] for c in overview["care_contexts"]]
    assert client.post(f"/api/hip/patients/{patient['id']}/care-contexts/link", headers=hims,
                       json={"care_context_ids": ids}).status_code == 200
    return client.get(f"/api/hip/patients/{patient['id']}", headers=hims).json()


# 1. HIP configuration
def test_hip_configuration(client, monkeypatch):
    hims = login(client, "hims")
    status = client.get("/api/hip/status", headers=hims).json()
    assert status["mode"] == "simulation" and status["hip_id"] == "SIM_HIP_001" and status["ready"] is True
    assert client.get("/api/hip/status", headers=login(client, "reception")).status_code == 403
    assert client.post("/api/hip/session-check", headers=hims).status_code == 403  # admin only

    monkeypatch.setattr(settings, "abdm_mode", "sandbox")
    status = client.get("/api/hip/status", headers=hims).json()
    failing = {c["label"] for c in status["checks"] if not c["ok"]}
    assert {"HIP ID", "Client credentials", "Bridge URL", "Patient OTP delivery"} <= failing and status["ready"] is False
    check = client.post("/api/hip/session-check", headers=login(client, "admin", "Admin1234")).json()
    assert check["status"] == "failed" and "ABDM_CLIENT_ID" in check["message"]
    assert client.post("/api/hip/simulate/abha", headers=hims, json={}).status_code in (409, 422)


# 2. patient identity linking (ABDM verifies the ABHA address against demographics)
def test_patient_identity_linking(client):
    patient, _, hims = shareable_prescription(client, abha_address="ramesh.k@sbx")
    url = f"/api/hip/patients/{patient['id']}"
    assert client.get(f"/api/patients/{patient['id']}", headers=hims).json()["abha_verified"] is False  # entered != verified

    client.post(f"{url}/link-token", headers=hims)
    assert client.get(url, headers=hims).json()["link"]["status"] == "failed"  # unknown to ABDM
    register_abha(client, hims, patient, "ramesh.k@sbx", year_of_birth=1990)
    client.post(f"{url}/link-token", headers=hims)
    failed = client.get(url, headers=hims).json()["link"]
    assert failed["status"] == "failed" and "do not match" in failed["message"]
    assert client.get(f"/api/patients/{patient['id']}", headers=hims).json()["abha_verified"] is False

    register_abha(client, hims, patient, "ramesh.k@sbx")
    assert client.post(f"{url}/link-token", headers=hims).json()["status"] == "pending"
    overview = client.get(url, headers=hims).json()
    with SessionLocal() as db:
        from app.models import HipPatientLink
        token = db.query(HipPatientLink).filter_by(patient_id=patient["id"]).one().link_token
    assert overview["link"]["status"] == "verified" and token and token not in json.dumps(overview)
    assert token not in json.dumps(client.get("/api/hip/transactions", headers=hims).json())
    registered = client.get(f"/api/patients/{patient['id']}", headers=hims).json()
    assert registered["abha_verified"] is True and registered["abha_verification_method"] == "abdm_link_token"
    assert registered["abha_number"] == "91123456789012"
    transactions = client.get("/api/hip/transactions?kind=on_generate_token", headers=hims).json()["items"]
    assert "linkToken" not in json.dumps(transactions[0]["details"])  # tokens are never stored in logs
    request = simulator.last_call(gateway.GENERATE_LINK_TOKEN_PATH)["body"]
    assert request == {"abhaAddress": "ramesh.k@sbx", "name": "Ramesh Kumar", "gender": "M", "yearOfBirth": 1978}


# 3 + 4. care context creation and HIP-initiated linking
def test_care_context_creation_and_linking(client):
    patient, record, hims = shareable_prescription(client, abha_address="ramesh.k@sbx")
    url = f"/api/hip/patients/{patient['id']}"
    contexts = client.get(url, headers=hims).json()["care_contexts"]
    assert len(contexts) == 1
    context = contexts[0]
    assert context["reference"] == f"TST-PRESCRIPTION-{record['source_id']}" and context["hi_type"] == "Prescription"
    assert context["display"].startswith("Prescription · ") and "Metformin" not in context["display"]
    assert context["link_status"] == "not_linked" and context["fhir_record_id"] == record["id"]

    with SessionLocal() as db:  # records that are not validated never become care contexts
        db.get(FhirRecord, record["id"]).validation_status = "invalid"
        db.commit()
    assert len(client.get(url, headers=hims).json()["care_contexts"]) == 1  # existing context is kept…
    blocked = client.post(f"{url}/care-contexts/link", headers=hims, json={"care_context_ids": [context["id"]]})
    assert blocked.status_code == 422 and "link token" in blocked.json()["detail"]  # …but not linked without verification
    with SessionLocal() as db:
        db.get(FhirRecord, record["id"]).validation_status = "valid"
        db.commit()

    register_abha(client, hims, patient, "ramesh.k@sbx")
    linked = link_via_token(client, hims, patient)["care_contexts"][0]
    assert linked["link_status"] == "linked" and linked["linked_abha_address"] == "ramesh.k@sbx"
    assert linked["link_method"] == "hip_initiated"
    body = simulator.last_call(gateway.ADD_CARE_CONTEXTS_PATH)["body"]
    assert body["abhaNumber"] == "91123456789012" and body["abhaAddress"] == "ramesh.k@sbx"
    assert body["patient"] == [{"referenceNumber": patient["patient_code"], "display": "Ramesh Kumar", "hiType": "Prescription",
                                "count": 1, "careContexts": [{"referenceNumber": context["reference"], "display": context["display"]}]}]


# 5 + 6. discovery request and response (signed callback over HTTP)
def test_discovery_request_and_response(client):
    patient, record, hims = shareable_prescription(client)
    body = {"transactionId": "txn-1", "patient": {"id": "ramesh.k@sbx", "name": "Ramesh Kumar", "gender": "M",
                                                  "yearOfBirth": 1978, "verifiedIdentifiers": [{"type": "MOBILE", "value": "+919876543210"}],
                                                  "unverifiedIdentifiers": []}}
    assert client.post(f"{CALLBACK}/hip/patient/care-context/discover", json=body).status_code == 401
    forged = jwt.encode({"iss": "x"}, rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256",
                        headers={"kid": simulator.KID})
    assert client.post(f"{CALLBACK}/hip/patient/care-context/discover", json=body,
                       headers=signed(Authorization=f"Bearer {forged}")).status_code == 401
    assert client.post(f"{CALLBACK}/hip/patient/care-context/discover", json=body,
                       headers=signed(**{"X-HIP-ID": "OTHER"})).status_code == 401
    assert client.post(f"{CALLBACK}/hip/patient/care-context/discover", json={"patient": {}}, headers=signed()).status_code == 400
    rejected = client.get("/api/hip/transactions?kind=callback_rejected", headers=hims).json()
    assert rejected["total"] == 3

    assert client.post(f"{CALLBACK}/hip/patient/care-context/discover", json=body, headers=signed()).status_code == 202
    reply = simulator.reply_to(gateway.ON_DISCOVER_PATH, "11111111-2222-3333-4444-555555555555")
    assert reply["transactionId"] == "txn-1" and reply["matchedBy"] == ["MOBILE"]
    assert reply["patient"][0]["referenceNumber"] == patient["patient_code"]
    assert reply["patient"][0]["careContexts"][0]["referenceNumber"].endswith(str(record["source_id"]))
    assert "Metformin" not in json.dumps(reply) and "dosage" not in json.dumps(reply)  # no record content revealed

    # Demographics must agree, and an unverified mobile is not enough.
    for patient_info in ({**body["patient"], "name": "Suresh Kumar"}, {**body["patient"], "yearOfBirth": 1990},
                         {**body["patient"], "verifiedIdentifiers": [],
                          "unverifiedIdentifiers": [{"type": "MOBILE", "value": "9876543210"}]}):
        client.post(f"{CALLBACK}/hip/patient/care-context/discover", headers=signed(),
                    json={"transactionId": "txn-x", "patient": patient_info})
        assert simulator.reply_to(gateway.ON_DISCOVER_PATH, "11111111-2222-3333-4444-555555555555")["error"]["code"] == "ABDM-1010"


def test_patient_initiated_linking(client):
    patient, _, hims = shareable_prescription(client)
    found = client.post(f"/api/hip/simulate/discover/{patient['id']}", headers=hims, json={"abha_address": "ramesh.k@sbx"}).json()
    groups, transaction = found["response"]["patient"], found["response"]["transactionId"]
    foreign = [{**groups[0], "careContexts": [{"referenceNumber": "TST-OTHER-99", "display": "x"}]}]
    refused = client.post("/api/hip/simulate/link-init", headers=hims,
                          json={"transaction_id": transaction, "abha_address": "ramesh.k@sbx", "patient": foreign}).json()
    assert "error" in refused["response"]

    init = client.post("/api/hip/simulate/link-init", headers=hims,
                       json={"transaction_id": transaction, "abha_address": "ramesh.k@sbx", "patient": groups}).json()
    link = init["response"]["link"]
    assert link["authenticationType"] == "MEDIATE" and link["meta"]["communicationHint"] == "XXXXXX3210"
    otp = init["sms_to_patient"]
    assert re.fullmatch(r"\d{6}", otp)
    wrong = client.post("/api/hip/simulate/link-confirm", headers=hims,
                        json={"link_reference": link["referenceNumber"], "otp": "000000" if otp != "000000" else "111111"}).json()
    assert wrong["error"]["code"] == "ABDM-1035"
    confirmed = client.post("/api/hip/simulate/link-confirm", headers=hims,
                            json={"link_reference": link["referenceNumber"], "otp": otp}).json()
    assert confirmed["patient"][0]["careContexts"] == groups[0]["careContexts"]
    overview = client.get(f"/api/hip/patients/{patient['id']}", headers=hims).json()
    assert overview["care_contexts"][0]["link_method"] == "patient_initiated"
    registered = client.get(f"/api/patients/{patient['id']}", headers=hims).json()
    assert registered["abha_address"] == "ramesh.k@sbx" and registered["abha_verification_method"] == "abdm_patient_linking"
    again = client.post("/api/hip/simulate/link-confirm", headers=hims, json={"link_reference": link["referenceNumber"], "otp": otp}).json()
    assert "error" in again  # one-time


def linked_patient(client):
    patient, record, hims = shareable_prescription(client, abha_address="ramesh.k@sbx")
    register_abha(client, hims, patient, "ramesh.k@sbx")
    overview = link_via_token(client, hims, patient)
    return patient, overview["care_contexts"][0], hims


def consent(client, hims, patient, **changes):
    body = {"patient_id": patient["id"], "date_from": "2020-01-01", "date_to": "2030-12-31",
            "erase_at": "2099-01-01T00:00:00", **changes}
    return client.post("/api/hip/simulate/consent", headers=hims, json=body).json()


# 7. consent verification
def test_consent_verification(client):
    patient, context, hims = linked_patient(client)
    granted = consent(client, hims, patient)
    assert granted["response"]["acknowledgement"] == {"status": "OK", "consentId": granted["consent_id"]}
    stored = client.get("/api/hip/consents", headers=hims).json()["items"][0]
    assert stored["status"] == "GRANTED" and stored["hi_types"] == ["Prescription"] and stored["hiu_id"] == "SIM_HIU_001"

    with SessionLocal() as db:
        p = db.get(Patient, patient["id"])
        reference = p.patient_code
    other_hip = simulator.grant_consent("ramesh.k@sbx", reference, [context["reference"]], ["Prescription"],
                                        "2020-01-01T00:00:00Z", "2030-01-01T00:00:00Z", "2099-01-01T00:00:00Z", hip_id="OTHER_HIP")
    assert other_hip["response"]["error"]["message"] == "Consent is for a different HIP"
    foreign = simulator.grant_consent("ramesh.k@sbx", reference, ["TST-PRESCRIPTION-999"], ["Prescription"],
                                      "2020-01-01T00:00:00Z", "2030-01-01T00:00:00Z", "2099-01-01T00:00:00Z")
    assert "does not hold" in foreign["response"]["error"]["message"]
    wrong_patient = simulator.grant_consent("someone.else@sbx", reference, [context["reference"]], ["Prescription"],
                                            "2020-01-01T00:00:00Z", "2030-01-01T00:00:00Z", "2099-01-01T00:00:00Z")
    assert "not linked to this ABHA address" in wrong_patient["response"]["error"]["message"]
    expired = consent(client, hims, patient, erase_at="2001-01-01T00:00:00")
    assert "expired" in expired["response"]["error"]["message"]
    statuses = {c["consent_id"]: c["status"] for c in client.get("/api/hip/consents", headers=hims).json()["items"]}
    assert list(statuses.values()).count("REJECTED") == 4

    revoked = consent(client, hims, patient, status="REVOKED", consent_id=granted["consent_id"])
    assert revoked["response"]["acknowledgement"]["status"] == "OK"
    assert {c["consent_id"]: c["status"] for c in client.get("/api/hip/consents", headers=hims).json()["items"]}[granted["consent_id"]] == "REVOKED"


# 8 + 9 + 10. FHIR bundle preparation, encryption, sharing and status
def test_health_information_sharing(client):
    patient, context, hims = linked_patient(client)
    granted = consent(client, hims, patient)
    result = client.post("/api/hip/simulate/health-information", headers=hims,
                         json={"consent_id": granted["consent_id"], "date_from": "2020-01-01", "date_to": "2030-12-31"}).json()
    assert result["on_request"]["hiRequest"]["sessionStatus"] == "ACKNOWLEDGED"
    [received] = result["received_by_hiu"]  # the simulated HIU decrypted it with its own keys
    assert received == {"careContextReference": context["reference"], "checksum_ok": True, "resourceType": "Bundle",
                        "profile": "PrescriptionRecord", "entries": received["entries"]}
    status = result["notification"]["notification"]["statusNotification"]
    assert status["sessionStatus"] == "TRANSFERRED" and status["hipId"] == "SIM_HIP_001"
    assert status["statusResponses"] == [{"careContextReference": context["reference"], "hiStatus": "DELIVERED", "description": "Done"}]
    tx = client.get("/api/hip/transactions?kind=health_information", headers=hims).json()["items"][0]
    assert tx["status"] == "success" and "1 of 1 record(s)" in tx["message"]
    assert "Metformin" not in json.dumps(client.get("/api/hip/transactions", headers=hims).json())  # never logged
    push = [c for c in simulator.outbox if c["path"] == gateway.HI_NOTIFY_PATH]
    assert push and all("content" not in json.dumps(c["body"]) for c in push)

    # Outside the requested date range: nothing is sent and the HIU is told why.
    early = client.post("/api/hip/simulate/health-information", headers=hims,
                        json={"consent_id": granted["consent_id"], "date_from": "2020-01-01", "date_to": "2020-12-31"}).json()
    assert early["received_by_hiu"] == []
    assert early["notification"]["notification"]["statusNotification"]["sessionStatus"] == "FAILED"

    # Request wider than the consent, and after revocation: refused at on-request, nothing pushed.
    wide = client.post("/api/hip/simulate/health-information", headers=hims,
                       json={"consent_id": granted["consent_id"], "date_from": "2010-01-01", "date_to": "2030-12-31"}).json()
    assert "outside the consented range" in wide["on_request"]["error"]["message"] and wide["received_by_hiu"] == []
    consent(client, hims, patient, status="REVOKED", consent_id=granted["consent_id"])
    revoked = client.post("/api/hip/simulate/health-information", headers=hims,
                          json={"consent_id": granted["consent_id"], "date_from": "2020-01-01", "date_to": "2030-12-31"}).json()
    assert "not active" in revoked["on_request"]["error"]["message"] and revoked["received_by_hiu"] == []
    counts = client.get("/api/hip/status", headers=hims).json()["counts"]
    assert counts["completed_transfers"] == 1 and counts["failed"] >= 3


def test_invalid_key_material_is_refused(client):
    patient, context, hims = linked_patient(client)
    granted = consent(client, hims, patient)
    with SessionLocal() as db:
        from app.hip import service
        from app.models import Hospital
        bad = {"transactionId": "t-bad", "hiRequest": {
            "consent": {"id": granted["consent_id"]}, "dateRange": {"from": "2020-01-01T00:00:00Z", "to": "2030-01-01T00:00:00Z"},
            "dataPushUrl": "sim://hiu", "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519",
                                                         "dhPublicKey": {"keyValue": base64.b64encode(b"\x04" + b"\x01" * 64).decode()},
                                                         "nonce": base64.b64encode(b"n" * 32).decode()}}}
        tx = service.health_information(db, db.get(Hospital, 1), {"request-id": "r"}, bad)
    assert tx.status == "failed" and "not on Curve25519" in tx.message


# 9. encryption (independent vectors from Fidelius CLI, the ABDM reference tool)
FIDELIUS = {
    "requester_private": "DMxHPri8d7IT23KgLk281zZenMfVHSdeamq0RhwlIBk=",
    "requester_public": "BAheD5rUqTy4V5xR4/6HWmYpopu5CO+KO8BECS0udNqUTSNo91TIqIIy1A4Vh+F94c+n9vAcwXU2bGcfsI5f69Y=",
    "requester_nonce": "6uj1RdDUbcpI3lVMZvijkMC8Te20O4Bcyz0SyivX8Eg=",
    "sender_private": "AYhVZpbVeX4KS5Qm/W0+9Ye2q3rnVVGmqRICmseWni4=",
    "sender_public": "BABVt+mpRLMXiQpIfEq6bj8hlXsdtXIxLsspmMgLNI1SR5mHgDVbjHO2A+U4QlMddGzqyEidzm1AkhtSxSO2Ahg=",
    "sender_nonce": "lmXgblZwotx+DfBgKJF0lZXtAXgBEYr5khh79Zytr2Y=",
    "plaintext": "Wormtail should never have been Potter cottage's secret keeper.",
    "ciphertext": "pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw==",
    "requester_x509": "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQIXg+a1Kk8uFecUeP+h1pmKaKbuQjvijvARAktLnTalE0jaPdUyKiCMtQOFYfhfeHPp/bwHMF1NmxnH7COX+vW",
}


def test_encryption_matches_fidelius_vectors():
    v = {k: base64.b64decode(s) if k not in ("plaintext", "ciphertext") else s for k, s in FIDELIUS.items()}
    sender, requester = (int.from_bytes(v[k], "big") for k in ("sender_private", "requester_private"))
    assert crypto.encrypt(v["plaintext"], sender, v["sender_nonce"], v["requester_public"], v["requester_nonce"]) == v["ciphertext"]
    assert crypto.decrypt(v["ciphertext"], requester, v["requester_nonce"], v["sender_public"], v["sender_nonce"]) == v["plaintext"]
    assert crypto.decrypt(v["ciphertext"], requester, v["requester_nonce"], crypto.X509_PREFIX + v["sender_public"],
                          v["sender_nonce"]) == v["plaintext"]
    assert crypto.X509_PREFIX + v["requester_public"] == v["requester_x509"]
    assert crypto.point_from_u(crypto.ladder(requester, crypto.BASE_U))[1:33] == v["requester_public"][1:33]
    with pytest.raises(crypto.CryptoError):
        crypto.u_from_public_key(b"\x04" + b"\x01" * 64)


def test_scalar_multiplication_matches_x25519():
    for _ in range(3):
        key = X25519PrivateKey.generate()
        scalar = bytearray(key.private_bytes_raw())
        scalar[0] &= 248
        scalar[31] = (scalar[31] & 127) | 64
        assert crypto.ladder(int.from_bytes(bytes(scalar), "little"), 9).to_bytes(32, "little") == key.public_key().public_bytes_raw()


# ---------- ABDM sandbox: contract tests against the documented requests (mocked HTTP) ----------

@pytest.fixture()
def sandbox(monkeypatch):
    """Sandbox mode with a recording transport standing in for dev.abdm.gov.in."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "gw-test", "alg": "RS256", "use": "sig"}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/gateway/v3/sessions"):
            return httpx.Response(202, json={"accessToken": "session-token-xyz", "expiresIn": 1200, "tokenType": "bearer"})
        if path.endswith("/gateway/v3/certs"):
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(202)

    for name, value in {"abdm_mode": "sandbox", "abdm_client_id": "SBX_TEST", "abdm_client_secret": "very-secret",
                        "abdm_hip_id": "IN0000TEST", "abdm_bridge_url": "https://hip.example.org"}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(gateway, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    gateway.reset()
    return {"calls": calls, "key": key}


def test_sandbox_session_and_headers(client, sandbox):
    admin = login(client, "admin", "Admin1234")
    assert client.post("/api/hip/session-check", headers=admin).json()["status"] == "success"
    session = sandbox["calls"][0]
    assert str(session.url) == "https://dev.abdm.gov.in/api/hiecm/gateway/v3/sessions" and session.method == "POST"
    assert json.loads(session.content) == {"clientId": "SBX_TEST", "clientSecret": "very-secret", "grantType": "client_credentials"}
    assert session.headers["X-CM-ID"] == "sbx" and re.fullmatch(r"[0-9a-f-]{36}", session.headers["REQUEST-ID"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", session.headers["TIMESTAMP"])

    assert client.post("/api/hip/bridge-url", headers=admin).json()["status"] == "success"
    bridge = sandbox["calls"][-1]
    assert bridge.method == "PATCH" and str(bridge.url).endswith("/api/hiecm/gateway/v3/bridge/url")
    assert json.loads(bridge.content) == {"url": "https://hip.example.org"}
    assert bridge.headers["Authorization"] == "Bearer session-token-xyz"
    logged = json.dumps(client.get("/api/hip/transactions", headers=login(client, "hims")).json())
    assert "very-secret" not in logged and "session-token-xyz" not in logged


def test_sandbox_link_token_request(client, sandbox):
    hims = login(client, "hims")
    patient = client.get("/api/patients?q=ramesh", headers=hims).json()["items"][0]
    client.patch(f"/api/patients/{patient['id']}", headers=login(client, "reception"), json={"abha_address": "ramesh.k@sbx"})
    assert client.post(f"/api/hip/patients/{patient['id']}/link-token", headers=hims).json()["status"] == "pending"
    call = sandbox["calls"][-1]
    assert str(call.url) == "https://dev.abdm.gov.in/api/hiecm/v3/token/generate-token"
    assert call.headers["X-HIP-ID"] == "IN0000TEST" and call.headers["X-CM-ID"] == "sbx"
    assert call.headers["Authorization"] == "Bearer session-token-xyz"
    assert json.loads(call.content) == {"abhaAddress": "ramesh.k@sbx", "name": "Ramesh Kumar", "gender": "M", "yearOfBirth": 1978}


def test_sandbox_callback_authentication(client, sandbox):
    """Callbacks are accepted only with a JWT signed by a key from /gateway/v3/certs."""
    body = {"transactionId": "t", "patient": {"id": "a@sbx", "name": "X", "gender": "M", "yearOfBirth": 1980}}
    good = jwt.encode({"exp": 9999999999}, sandbox["key"], algorithm="RS256", headers={"kid": "gw-test"})
    other = jwt.encode({"exp": 9999999999}, rsa.generate_private_key(public_exponent=65537, key_size=2048),
                       algorithm="RS256", headers={"kid": "gw-test"})
    headers = {"X-HIP-ID": "IN0000TEST", "REQUEST-ID": "r-1"}
    url = f"{CALLBACK}/hip/patient/care-context/discover"
    assert client.post(url, json=body, headers={**headers, "Authorization": f"Bearer {other}"}).status_code == 401
    assert client.post(url, json=body, headers={**headers, "Authorization": f"Bearer {good}"}).status_code == 202
    assert any(str(c.url).endswith("/api/hiecm/gateway/v3/certs") for c in sandbox["calls"])
    on_discover = sandbox["calls"][-1]
    assert str(on_discover.url) == "https://dev.abdm.gov.in/api/hiecm/user-initiated-linking/v3/patient/care-context/on-discover"
    assert json.loads(on_discover.content)["response"]["requestId"] == "r-1"
    # Without an SMS gateway, patient-initiated linking is refused rather than faked in sandbox mode.
    with SessionLocal() as db:
        assert db.query(HipTransaction).filter_by(kind="discover").one().mode == "sandbox"


@pytest.mark.skipif(not (os.getenv("ABDM_LIVE_CLIENT_ID") and os.getenv("ABDM_LIVE_CLIENT_SECRET")),
                    reason="set ABDM_LIVE_CLIENT_ID/ABDM_LIVE_CLIENT_SECRET to test against the real ABDM sandbox")
def test_live_sandbox_session_and_certs(monkeypatch):
    """Real ABDM sandbox: obtain a gateway session and the signing keys (no patient data involved)."""
    monkeypatch.setattr(settings, "abdm_mode", "sandbox")
    monkeypatch.setattr(settings, "abdm_client_id", os.environ["ABDM_LIVE_CLIENT_ID"])
    monkeypatch.setattr(settings, "abdm_client_secret", os.environ["ABDM_LIVE_CLIENT_SECRET"])
    gateway.reset()
    assert gateway.session_token()
    assert gateway.jwks()["keys"]

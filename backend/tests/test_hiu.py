"""Phase 5: ABDM HIU. Full simulated consent + retrieval workflow, security rules and sandbox contract tests."""

import base64
import json
from datetime import date, timedelta

import jwt
import pytest

from app.config import settings
from app.database import SessionLocal
from app.fhir_validation import validator_available
from app.hip import gateway, simulator
from app.hiu import ai as hiu_ai, records, service
from app.models import HiuConsentRequest, RetrievedRecord, utcnow
from tests.conftest import login
from tests.test_hip import register_abha, sandbox  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


@pytest.fixture(autouse=True)
def hiu_env(monkeypatch):
    monkeypatch.setattr(settings, "abdm_mode", "simulation")
    monkeypatch.setattr(settings, "hiu_auto_validate", False)
    simulator.reset()
    gateway.reset()
    calls = []

    def fake_model(system, payload, output_format):  # no patient data leaves the machine in tests
        calls.append(payload)
        if output_format is hiu_ai.RequestSuggestion:
            return hiu_ai.RequestSuggestion(purpose_code="CAREMGT", hi_types=["DiagnosticReport", "Prescription"],
                                            date_from="2024-01-01", date_to=TODAY, missing_information=[],
                                            explanation="Earlier HbA1c results help assess diabetes control.")
        ids = [r["record_id"] for r in payload["records"]]
        item = lambda text, sources: hiu_ai.SummaryItem(text=text, source_record_ids=sources)  # noqa: E731
        return hiu_ai.RecordsSummary(overview=[item("HbA1c reported twice at Sunrise Clinic.", ids),
                                               item("Unsupported statement.", [999])],
                                     consultations=[], prescriptions=[],
                                     laboratory=[item("HbA1c 8.1 % then 7.2 %.", ids)])
    monkeypatch.setattr(hiu_ai, "call_model", fake_model)
    yield calls
    simulator.reset()


def verified_patient(client):
    hims = login(client, "hims")
    patient = client.get("/api/patients?q=ramesh", headers=hims).json()["items"][0]
    client.patch(f"/api/patients/{patient['id']}", headers=login(client, "reception"), json={"abha_address": "ramesh.k@sbx"})
    register_abha(client, hims, patient, "ramesh.k@sbx")
    client.post(f"/api/hip/patients/{patient['id']}/link-token", headers=hims)  # ABDM verifies the ABHA address
    return client.get(f"/api/patients/{patient['id']}", headers=hims).json()


def submit(client, headers, patient, **changes):
    body = {"patient_id": patient["id"], "purpose_code": "CAREMGT", "hi_types": ["DiagnosticReport", "Prescription"],
            "date_from": "2024-01-01", "date_to": TODAY, "reason": "Assess diabetes control", **changes}
    return client.post("/api/hiu/requests", headers=headers, json=body)


def test_complete_hiu_workflow(client, hiu_env):
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")

    # 1. select a patient (ABHA verified through ABDM in Phase 4)
    patient = verified_patient(client)
    assert patient["abha_verified"] and patient["abha_verification_method"] == "abdm_link_token"

    # 2. prepare: AI suggests, nothing is created
    suggestion = client.post("/api/hiu/suggest", headers=doctor, json={"patient_id": patient["id"], "reason": "Diabetes control"}).json()
    assert suggestion["purpose_code"] == "CAREMGT" and suggestion["hi_types"] == ["DiagnosticReport", "Prescription"]
    assert hiu_env[0]["patient"] == {"age_years": date.today().year - 1978, "gender": "M"}  # minimal context, no name/ABHA
    assert client.get("/api/hiu/requests", headers=doctor).json()["total"] == 0

    # 3. submit the consent request (staff decide; patient consent comes next)
    request = submit(client, doctor, patient, ai_suggestion=suggestion).json()
    assert request["status"] == "pending_consent" and request["consent_request_id"]
    init = simulator.last_call(gateway.CONSENT_INIT_PATH)["body"]["consent"]
    assert init["patient"] == {"id": "ramesh.k@sbx"} and init["hiu"] == {"id": "SIM_HIU_LOCAL"}
    assert init["requester"] == {"name": "Dr. Anil Mehta", "identifier": {"type": "REGNO", "value": "DMC-45821",
                                                                         "system": "https://www.mciindia.org"}}
    assert init["purpose"] == {"text": "Care Management", "code": "CAREMGT", "refUri": "www.abdm.gov.in"}
    assert init["permission"]["accessMode"] == "VIEW" and init["permission"]["dateRange"]["from"].startswith("2024-01-01T00:00")

    # 4-6. the patient grants only lab reports; artefact fetched and validated
    granted = client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor,
                          json={"grant": True, "hi_types": ["DiagnosticReport"]}).json()
    assert granted["status"] == "received"
    detail = client.get(f"/api/hiu/requests/{request['id']}", headers=hims).json()
    [artefact] = detail["artefacts"]
    assert artefact["status"] == "valid" and artefact["hi_types"] == ["DiagnosticReport"] and artefact["hip_id"] == "SIM_EXT_HIP_01"
    ack = simulator.last_call(gateway.CONSENT_HIU_ON_NOTIFY_PATH)["body"]
    assert ack["acknowledgement"] == [{"status": "OK", "consentId": artefact["artefact_id"]}]
    assert simulator.last_call(gateway.CONSENT_FETCH_PATH)["body"] == {"consentId": artefact["artefact_id"]}

    # 7. health information request with fresh key material and a one-time push URL
    hi = simulator.last_call(gateway.HI_REQUEST_PATH)["body"]["hiRequest"]
    key = base64.b64decode(hi["keyMaterial"]["dhPublicKey"]["keyValue"])
    assert len(key) == 65 and key[0] == 4 and hi["keyMaterial"]["curve"] == "Curve25519"
    assert hi["dataPushUrl"].startswith("sim://hiu/api/hiu/data-push/") and hi["consent"]["id"] == artefact["artefact_id"]
    [data_request] = detail["data_requests"]
    assert data_request["status"] == "received" and data_request["pages"] == "1/1"
    kinds = [t["kind"] for t in detail["timeline"]]
    assert kinds[:2] == ["hiu_consent_init", "hiu_on_init"]
    assert {"hiu_notify", "hiu_consent_fetch", "hiu_on_fetch", "hiu_data_request", "hiu_on_request", "hiu_data_push"} <= set(kinds)

    # 8-9. asynchronous push decrypted; only consented, in-range records were accepted
    with SessionLocal() as db:
        rows = db.query(RetrievedRecord).all()
        assert len(rows) == 2 and {r.category for r in rows} == {"Laboratory report"}
        assert all(r.checksum_ok and "HbA1c" not in r.content for r in rows)  # sealed at rest
        original = records.bundle_json(rows[0])
        assert original["resourceType"] == "Bundle" and original["type"] == "document"  # preserved as received
    notify = [c["body"] for c in simulator.outbox if c["path"] == gateway.HI_NOTIFY_PATH][-1]["notification"]
    assert notify["notifier"] == {"type": "HIU", "id": "SIM_HIU_LOCAL"}
    assert notify["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    assert {s["hiStatus"] for s in notify["statusNotification"]["statusResponses"]} == {"OK"}

    # 10. FHIR checks ran on receipt (full HL7 validation: see test_hl7_validation_of_retrieved_record)
    view = client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()
    assert {r["validation_status"] for r in view["records"]} == {"not_validated"}

    # 11-12. authorised display, timeline and comparable lab trend
    assert [r["source_name"] for r in view["records"]] == ["Sunrise Clinic (SIMULATED)"] * 2
    assert all(r["mode"] == "simulation" for r in view["records"])
    [trend] = view["lab_trends"]
    assert trend["test"] == "HbA1c" and trend["comparable"] and trend["change"] == -0.9
    assert [p["value"] for p in trend["points"]] == [8.1, 7.2]
    assert len(view["timeline"]) == 2
    one = client.get(f"/api/hiu/records/{view['records'][0]['id']}", headers=doctor).json()
    assert one["facts"]["reports"][0]["name"] == "HbA1c" and one["original"]["entry"][0]["resource"]["resourceType"] == "Composition"
    assert client.get(f"/api/hiu/patients/{patient['id']}/records", headers=hims).status_code == 403  # no clinical access
    assert client.get("/api/hiu/requests", headers=login(client, "reception")).status_code == 403
    audit = client.get("/api/audit-logs?action=hiu.record", headers=login(client, "admin", "Admin1234")).json()
    assert {i["action"] for i in audit["items"]} == {"hiu.records_view", "hiu.record_view"}

    # 13. AI summary: every item cites retrieved records; unsupported items are dropped
    summary = client.post(f"/api/hiu/patients/{patient['id']}/summary", headers=doctor).json()
    assert summary["removed_unsupported_items"] == 1 and len(summary["overview"]) == 1
    assert set(summary["laboratory"][0]["source_record_ids"]) == {r["id"] for r in view["records"]}
    assert client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()["summary"]["outdated"] is False

    dashboard = client.get("/api/hiu/dashboard", headers=hims).json()["counts"]
    assert dashboard["received"] == 1 and dashboard["records"] == 2


def test_request_rules(client):
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    unverified = client.get("/api/patients?q=sita", headers=doctor).json()["items"][0]
    refused = submit(client, doctor, unverified)
    assert refused.status_code == 422 and "verified" in refused.json()["detail"]
    patient = verified_patient(client)
    assert submit(client, doctor, patient, hi_types=["Everything"]).status_code == 422
    assert submit(client, doctor, patient, date_from="2030-01-01", date_to="2031-01-01").status_code == 422
    assert submit(client, hims, patient).status_code == 403  # no doctor profile: must name the requesting doctor
    on_behalf = submit(client, hims, patient, requester_doctor_id=2)
    assert on_behalf.status_code == 201
    assert simulator.last_call(gateway.CONSENT_INIT_PATH)["body"]["consent"]["requester"]["name"] == "Dr. Sunita Rao"


def test_denied_consent_stops_everything(client):
    doctor = login(client, "dr.mehta")
    request = submit(client, doctor, verified_patient(client)).json()
    denied = client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor,
                         json={"grant": False, "reason": "Not now"}).json()
    assert denied["status"] == "denied" and "Not now" in denied["status_message"]
    assert simulator.last_call(gateway.CONSENT_FETCH_PATH) is None and simulator.last_call(gateway.HI_REQUEST_PATH) is None
    with SessionLocal() as db:
        assert db.query(RetrievedRecord).count() == 0


def test_revocation_and_expiry_erase_records(client):
    doctor = login(client, "dr.mehta")
    patient = verified_patient(client)
    request = submit(client, doctor, patient).json()
    client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True})
    with SessionLocal() as db:
        assert db.query(RetrievedRecord).count() == 3  # 2 lab reports + 1 prescription in range
    revoked = client.post(f"/api/hiu/simulate/requests/{request['id']}/revoke", headers=doctor).json()
    assert revoked["status"] == "revoked"
    assert client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()["records"] == []

    second = submit(client, doctor, patient).json()
    client.post(f"/api/hiu/simulate/requests/{second['id']}/decide", headers=doctor, json={"grant": True})
    with SessionLocal() as db:
        for row in db.query(RetrievedRecord).all():
            row.erase_at = utcnow() - timedelta(minutes=1)  # dataEraseAt has passed
        db.commit()
    assert client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()["records"] == []


def test_push_url_is_single_use_and_scoped(client):
    doctor = login(client, "dr.mehta")
    request = submit(client, doctor, verified_patient(client)).json()
    client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True})
    token = simulator.last_call(gateway.HI_REQUEST_PATH)["body"]["hiRequest"]["dataPushUrl"].rsplit("/", 1)[-1]
    replay = client.post(f"/api/hiu/data-push/{token}", json={"transactionId": "x", "entries": []})
    assert replay.status_code == 409  # key destroyed after receipt: the URL cannot be reused
    assert client.post("/api/hiu/data-push/not-a-token", json={}).status_code == 404

    # a fresh request under the same valid consent; an entry outside the consent is refused
    with SessionLocal() as db:
        artefact = db.query(HiuConsentRequest).get(request["id"]).artefacts[0]
        data_request = service.request_data(db, artefact)
        foreign = {"pageCount": 1, "transactionId": None, "keyMaterial": {"dhPublicKey": {"keyValue": "AA=="}, "nonce": "AA=="},
                   "entries": [{"careContextReference": "NOT-IN-CONSENT", "content": "x", "checksum": "y"}]}
        status, message, ids = service.receive_push(db, data_request.push_token, foreign)
    assert status == 202 and ids == []
    notify = [c["body"] for c in simulator.outbox if c["path"] == gateway.HI_NOTIFY_PATH][-1]["notification"]
    assert notify["statusNotification"]["sessionStatus"] == "FAILED"
    assert notify["statusNotification"]["statusResponses"][0]["hiStatus"] == "ERRORED"


def test_artefact_validation_rules(client):
    doctor = login(client, "dr.mehta")
    request_out = submit(client, doctor, verified_patient(client)).json()
    with SessionLocal() as db:
        request = db.get(HiuConsentRequest, request_out["id"])
        good = {"consentId": "a1", "hiu": {"id": "SIM_HIU_LOCAL"}, "patient": {"id": "ramesh.k@sbx"}, "hip": {"id": "H"},
                "hiTypes": ["Prescription"], "purpose": {"code": "CAREMGT"}, "careContexts": [{"careContextReference": "c"}],
                "permission": {"accessMode": "VIEW", "dateRange": {"from": "2024-06-01T00:00:00Z", "to": f"{TODAY}T10:00:00Z"},
                               "dataEraseAt": "2099-01-01T00:00:00Z"}}
        assert service.artefact_problem(request, "a1", "GRANTED", good) is None
        cases = {
            "Artefact status is DENIED": ("DENIED", {}),
            "Artefact is for a different HIU": ("GRANTED", {"hiu": {"id": "OTHER"}}),
            "Artefact is for a different patient": ("GRANTED", {"patient": {"id": "x@sbx"}}),
            "Artefact covers record types that were not requested": ("GRANTED", {"hiTypes": ["DischargeSummary"]}),
            "Artefact purpose differs from the request": ("GRANTED", {"purpose": {"code": "BTG"}}),
            "Artefact date range is wider than requested": ("GRANTED", {"permission": {**good["permission"],
                                                                                        "dateRange": {"from": "2010-01-01T00:00:00Z", "to": f"{TODAY}T00:00:00Z"}}}),
            "Artefact has already expired": ("GRANTED", {"permission": {**good["permission"], "dataEraseAt": "2001-01-01T00:00:00Z"}}),
        }
        for message, (status, change) in cases.items():
            assert service.artefact_problem(request, "a1", status, {**good, **change}) == message


def test_hiu_callbacks_require_gateway_token(client):
    body = {"notification": {"consentRequestId": "x", "status": "GRANTED"}}
    url = "/api/v3/hiu/consent/request/notify"
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": f"Bearer {simulator.issue_token()}", "X-HIU-ID": "OTHER"}).status_code == 401
    ok = client.post(url, json=body, headers={"Authorization": f"Bearer {simulator.issue_token()}", "X-HIU-ID": "SIM_HIU_LOCAL",
                                              "REQUEST-ID": "r"})
    assert ok.status_code == 202


@pytest.mark.skipif(not validator_available(), reason="HL7 validator not installed")
def test_hl7_validation_of_retrieved_record(client):
    doctor = login(client, "dr.mehta")
    patient = verified_patient(client)
    request = submit(client, doctor, patient, hi_types=["DiagnosticReport"]).json()
    client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True})
    record = client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()["records"][0]
    validated = client.post(f"/api/hiu/records/{record['id']}/validate", headers=doctor).json()
    assert validated["validation_status"] == "valid"


# ---------- sandbox contract (documented URLs and headers; HTTP mocked) ----------

def test_sandbox_consent_and_data_request_contract(client, sandbox, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "abdm_hiu_id", "IN0000HIU")
    with SessionLocal() as db:
        from app.models import Patient
        p = db.get(Patient, 1)
        p.abha_address, p.abha_verified = "ramesh.k@sbx", True
        db.commit()
    doctor = login(client, "dr.mehta")
    request = submit(client, doctor, {"id": 1}).json()
    init = sandbox["calls"][-1]
    assert str(init.url) == "https://dev.abdm.gov.in/api/hiecm/consent/v3/request/init"
    assert init.headers["X-HIU-ID"] == "IN0000HIU" and init.headers["X-CM-ID"] == "sbx"
    assert init.headers["Authorization"] == "Bearer session-token-xyz"
    assert json.loads(init.content)["consent"]["hiu"] == {"id": "IN0000HIU"}

    token = jwt.encode({"exp": 9999999999}, sandbox["key"], algorithm="RS256", headers={"kid": "gw-test"})
    callback = {"Authorization": f"Bearer {token}", "X-HIU-ID": "IN0000HIU", "REQUEST-ID": "cb-1"}
    client.post("/api/v3/hiu/consent/request/on-init", headers=callback,
                json={"consentRequest": {"id": "cr-1"}, "error": None, "response": {"requestId": init.headers["REQUEST-ID"]}})
    client.post("/api/v3/hiu/consent/request/notify", headers=callback,
                json={"notification": {"consentRequestId": "cr-1", "status": "GRANTED", "consentArtefacts": [{"id": "art-1"}]}})
    on_notify, fetch = sandbox["calls"][-2:]
    assert str(on_notify.url).endswith("/api/hiecm/consent/v3/request/hiu/on-notify")
    assert json.loads(on_notify.content) == {"acknowledgement": [{"status": "OK", "consentId": "art-1"}],
                                              "response": {"requestId": "cb-1"}}
    assert str(fetch.url).endswith("/api/hiecm/consent/v3/fetch") and json.loads(fetch.content) == {"consentId": "art-1"}

    detail = {"consentId": "art-1", "hiu": {"id": "IN0000HIU"}, "hip": {"id": "IN0000OTHER"}, "patient": {"id": "ramesh.k@sbx"},
              "hiTypes": ["Prescription"], "purpose": {"code": "CAREMGT"}, "careContexts": [{"patientReference": "p", "careContextReference": "c"}],
              "permission": {"accessMode": "VIEW", "dateRange": {"from": "2024-01-01T00:00:00Z", "to": f"{TODAY}T00:00:00Z"},
                             "dataEraseAt": "2099-01-01T00:00:00Z"}}
    client.post("/api/v3/hiu/consent/on-fetch", headers=callback,
                json={"consent": {"status": "GRANTED", "consentDetail": detail, "signature": "s"},
                      "response": {"requestId": fetch.headers["REQUEST-ID"]}})
    hi = sandbox["calls"][-1]
    assert str(hi.url) == "https://dev.abdm.gov.in/api/hiecm/data-flow/v3/health-information/request"
    assert hi.headers["X-HIU-ID"] == "IN0000HIU"
    push_url = json.loads(hi.content)["hiRequest"]["dataPushUrl"]
    assert push_url.startswith("https://hip.example.org/api/hiu/data-push/") and len(push_url.rsplit("/", 1)[-1]) >= 40
    assert client.get(f"/api/hiu/requests/{request['id']}", headers=doctor).json()["request"]["status"] == "retrieving"

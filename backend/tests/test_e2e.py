"""Phase 6 end-to-end scenarios A-D through the API, with the real HL7 FHIR validator and the ABDM simulator.

Simulation stands in for the ABDM gateway, consent manager, the patient's ABHA app and other providers;
nothing here claims a real ABDM exchange.
"""

import copy
from datetime import date

import pytest

from app import assistant, extraction
from app.config import settings
from app.fhir_validation import validator_available
from app.hip import gateway, simulator
from app.hiu import ai as hiu_ai
from tests.conftest import login
from tests.test_clinical import MEDICINES, start_consultation
from tests.test_documents import LAB, accept_all, upload
from tests.test_hip import register_abha

pytestmark = pytest.mark.skipif(not validator_available(), reason="HL7 validator not installed")


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for name, value in {"abdm_mode": "simulation", "hiu_auto_validate": False, "upload_dir": tmp_path}.items():
        monkeypatch.setattr(settings, name, value)
    simulator.reset()
    gateway.reset()
    monkeypatch.setattr(extraction, "call_model",
                        lambda blocks: extraction.Extraction[extraction.AIField].model_validate(copy.deepcopy(LAB)))
    monkeypatch.setattr(hiu_ai, "call_model", lambda system, payload, fmt: hiu_ai.RecordsSummary(
        overview=[hiu_ai.SummaryItem(text="HbA1c documented twice.", source_record_ids=[r["record_id"] for r in payload["records"]])],
        consultations=[], prescriptions=[], laboratory=[]))
    monkeypatch.setattr(assistant, "call_model", lambda system, content, fmt: assistant.Summary(
        consultations=[], conditions=[], medications=[], laboratory=[], observations=[], external_records=[],
        missing_information=["Allergies not recorded"]))
    yield
    simulator.reset()


def validate(client, headers, source_type, source_id) -> dict:
    record = client.post("/api/fhir/records", headers=headers, json={"source_type": source_type, "source_id": source_id}).json()
    validated = client.post(f"/api/fhir/records/{record['id']}/validate", headers=headers).json()
    errors = [i for i in validated["validation_issues"] if i["severity"] == "error"]
    assert validated["validation_status"] == "valid" and validated["hip_eligible"], errors
    return validated


def verify_and_link(client, hims, patient, address):
    client.patch(f"/api/patients/{patient['id']}", headers=login(client, "reception"), json={"abha_address": address})
    register_abha(client, hims, patient, address)
    assert client.post(f"/api/hip/patients/{patient['id']}/link-token", headers=hims).json()["status"] == "pending"
    overview = client.get(f"/api/hip/patients/{patient['id']}", headers=hims).json()
    assert overview["link"]["status"] == "verified"
    unlinked = [c["id"] for c in overview["care_contexts"] if c["link_status"] != "linked"]
    client.post(f"/api/hip/patients/{patient['id']}/care-contexts/link", headers=hims, json={"care_context_ids": unlinked})
    return client.get(f"/api/hip/patients/{patient['id']}", headers=hims).json()["care_contexts"]


def share(client, hims, patient, hi_type):
    consent = client.post("/api/hip/simulate/consent", headers=hims, json={
        "patient_id": patient["id"], "hi_types": [hi_type], "date_from": "2020-01-01", "date_to": "2030-12-31",
        "erase_at": "2099-01-01T00:00:00"}).json()
    assert consent["response"]["acknowledgement"]["status"] == "OK"
    result = client.post("/api/hip/simulate/health-information", headers=hims, json={
        "consent_id": consent["consent_id"], "date_from": "2020-01-01", "date_to": "2030-12-31"}).json()
    status = result["notification"]["notification"]["statusNotification"]
    assert status["sessionStatus"] == "TRANSFERRED"
    return result


def test_scenario_a_prescription_shared_through_hip(client):
    reception, doctor, hims = login(client, "reception"), login(client, "dr.mehta"), login(client, "hims")
    patient = client.post("/api/patients", headers=reception, json={
        "full_name": "Anita Desai", "gender": "F", "mobile": "9822012345", "date_of_birth": "1984-02-14"}).json()
    consult = start_consultation(client, doctor, patient["id"])
    client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={
        "chief_complaint": "Increased thirst", "vitals": {"pulse": 84, "systolic_bp": 132, "diastolic_bp": 84},
        "diagnoses": [{"display": "Type 2 diabetes mellitus", "code": "E11.9"}]})
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    client.post(f"/api/prescriptions/{rx['id']}/submit", headers=doctor)
    assert client.post(f"/api/prescriptions/{rx['id']}/approve", headers=doctor).json()["status"] == "approved"
    validate(client, hims, "prescription", rx["id"])
    contexts = verify_and_link(client, hims, patient, "anita.d@sbx")
    assert [c["hi_type"] for c in contexts] == ["Prescription"] and contexts[0]["link_status"] == "linked"
    result = share(client, hims, patient, "Prescription")
    assert [r["profile"] for r in result["received_by_hiu"]] == ["PrescriptionRecord"] and result["received_by_hiu"][0]["checksum_ok"]


def test_scenario_b_lab_report_with_loinc_shared_through_hip(client):
    doctor, lab, rao, hims = (login(client, u) for u in ("dr.mehta", "labtech", "dr.rao", "hims"))
    patient = client.get("/api/patients?q=sita", headers=doctor).json()["items"][0]
    consult = start_consultation(client, doctor, patient["id"])
    # map the catalogue parameter to LOINC with the existing mapping project, then confirm it in the catalogue
    tests = client.get("/api/lab/tests", headers=lab).json()
    fbs = next(t for t in tests if t["code"] == "FBS")
    suggestion = client.get("/api/terminology/loinc/suggest", params={"q": "Glucose fasting"}, headers=lab).json()
    term = client.get(f"/api/terminology/loinc/{fbs['parameters'][0]['loinc_code']}", headers=lab).json()
    assert suggestion["code"] and term["status"] == "ACTIVE"
    order = client.post("/api/lab/orders", headers=doctor, json={"encounter_id": consult["id"], "test_ids": [fbs["id"]]}).json()[0]
    client.post(f"/api/lab/orders/{order['id']}/sample", headers=lab, json={})
    client.put(f"/api/lab/orders/{order['id']}/report", headers=lab,
               json={"results": [{"code": "GLU", "value": "126"}], "conclusion": "Raised fasting glucose"})
    client.post(f"/api/lab/orders/{order['id']}/report/submit", headers=lab)
    assert client.post(f"/api/lab/orders/{order['id']}/report/approve", headers=rao).json()["status"] == "completed"
    record = validate(client, hims, "lab_order", order["id"])
    observation = next(e["resource"] for e in record["bundle"]["entry"] if e["resource"]["resourceType"] == "Observation")
    assert observation["code"]["coding"][0] == {"system": "http://loinc.org", "code": "1558-6", "display": term["display"]}
    verify_and_link(client, hims, patient, "sita.devi@sbx")
    result = share(client, hims, patient, "DiagnosticReport")
    assert [r["profile"] for r in result["received_by_hiu"]] == ["DiagnosticReportRecord"]


def test_scenario_c_previous_records_retrieved_through_hiu(client):
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    patient = client.get("/api/patients?q=ramesh", headers=doctor).json()["items"][0]
    verify_and_link(client, hims, patient, "ramesh.k@sbx")  # ABDM-verified ABHA needed to request records
    request = client.post("/api/hiu/requests", headers=doctor, json={
        "patient_id": patient["id"], "purpose_code": "CAREMGT", "hi_types": ["DiagnosticReport"],
        "date_from": "2024-01-01", "date_to": date.today().isoformat()}).json()
    assert request["status"] == "pending_consent"
    granted = client.post(f"/api/hiu/simulate/requests/{request['id']}/decide", headers=doctor, json={"grant": True}).json()
    assert granted["status"] == "received"
    view = client.get(f"/api/hiu/patients/{patient['id']}/records", headers=doctor).json()
    assert len(view["records"]) == 2 and view["lab_trends"][0]["comparable"]
    validated = client.post(f"/api/hiu/records/{view['records'][0]['id']}/validate", headers=doctor).json()
    assert validated["validation_status"] == "valid"
    summary = client.post(f"/api/hiu/patients/{patient['id']}/summary", headers=doctor).json()
    assert summary["overview"][0]["source_record_ids"]
    overview = client.get(f"/api/assistant/patients/{patient['id']}/overview", headers=doctor).json()
    assert len(overview["briefing"]["external_records"]) == 2  # retrieved records feed the assistant
    history = client.post(f"/api/assistant/patients/{patient['id']}/summary", headers=doctor).json()
    assert history["missing_information"] == ["Allergies not recorded"]


def test_scenario_d_ai_document_entry(client):
    lab, rao = login(client, "labtech"), login(client, "dr.rao")
    doc = client.get(f"/api/documents/{upload(client, lab, b'LAB REPORT').json()['id']}", headers=lab).json()
    assert doc["extraction_status"] == "completed" and len(doc["data"]["lab_results"]) == 2
    data = accept_all(doc["data"])
    data["lab_results"][1]["value"]["value"] = "450"  # correction of a misread value
    data["report_conclusion"] = {"value": "Mild anaemia"}  # missing value added by staff
    patient = client.get("/api/patients?q=ramesh", headers=lab).json()["items"][0]
    doc = client.put(f"/api/documents/{doc['id']}", headers=lab, json={
        "data": data, "patient_id": patient["id"], "author_doctor_id": client.get("/api/auth/me", headers=rao).json()["doctor_id"]}).json()
    assert doc["data"]["lab_results"][1]["value"]["source"] == "manual"
    for index in (0, 1):
        doc = client.post(f"/api/documents/{doc['id']}/lab-results/{index}/loinc", headers=lab, json={"action": "confirm"}).json()
    assert doc["requirements"] == []
    client.post(f"/api/documents/{doc['id']}/submit", headers=lab)
    approved = client.post(f"/api/documents/{doc['id']}/approve", headers=rao).json()
    validated = client.post(f"/api/fhir/records/{approved['fhir_record_id']}/validate", headers=rao).json()
    assert validated["validation_status"] == "valid", [i for i in validated["validation_issues"] if i["severity"] == "error"]

"""Phase 3: AI document extraction, review, LOINC mapping, approval and FHIR generation/validation."""

import copy

import pytest

from app import extraction, fhir_validation, loinc
from app.config import settings
from tests.conftest import login

PATIENT_KEYS = ["name", "age", "gender", "date_of_birth", "mobile", "abha_number", "hospital_patient_id"]
DOCTOR_KEYS = ["name", "registration_number", "qualification", "specialization", "facility_name"]
ENCOUNTER_KEYS = ["visit_date", "admission_date", "discharge_date", "chief_complaint", "history_of_present_illness",
                  "examination_findings", "hospital_course", "treatment_plan", "follow_up", "instructions"]


def f(value=None, confidence=0.95, evidence="as printed"):
    return {"value": value, "confidence": confidence if value else 0, "evidence": evidence if value else None}


def blank(keys):
    return {k: f() for k in keys}


def ai_output(document_type, **overrides):
    data = {"document_type": document_type, "document_date": f("2026-10-30"), "patient": blank(PATIENT_KEYS),
            "doctor": blank(DOCTOR_KEYS), "encounter": blank(ENCOUNTER_KEYS), "diagnoses": [], "allergies": [],
            "vitals": [], "medications": [], "lab_results": [], "immunizations": [],
            "report_conclusion": f(), "notes": f()}
    data["patient"]["name"] = f("Ramesh Kumar")
    data.update(overrides)
    return data


PRESCRIPTION = ai_output("prescription", medications=[
    {"name": f("Metformin 500 mg"), "dosage": f("1 tab"), "route": f("oral"), "frequency": f("1-0-1"),
     "duration": f("30 days"), "quantity": f("60"), "instructions": f("after food")},
    # handwriting was hard to read: low confidence, must be reviewed
    {"name": f("Amlodipine 5 mg", 0.55, "Amlo...5"), "dosage": f("1 tab", 0.6), "route": f(), "frequency": f("OD", 0.5),
     "duration": f("1 week"), "quantity": f(), "instructions": f()}],
    diagnoses=[{"text": f("Type 2 diabetes mellitus"), "icd10_code": f()}])

LAB = ai_output("lab_report", lab_results=[
    {"test_name": f("Haemoglobin"), "value": f("10.5"), "unit": f("g/dL"), "reference_range": f("12-17"),
     "flag": f("L"), "panel_name": f("CBC")},
    {"test_name": f("Platelet count"), "value": f("480"), "unit": f("10^3/uL"), "reference_range": f("150 - 450"),
     "flag": f("H"), "panel_name": f("CBC")}])

FAKE_DOCUMENTS = {b"PRESCRIPTION": PRESCRIPTION, b"LAB": LAB}


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch, tmp_path):
    """No document leaves the machine in tests: the model call returns a fixed extraction per sample file."""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    calls = []

    def call_model(blocks):
        calls.append(blocks)
        text = blocks[0].get("text", "").encode()
        for marker, output in FAKE_DOCUMENTS.items():
            if marker in text:
                return extraction.Extraction[extraction.AIField].model_validate(copy.deepcopy(output))
        raise extraction.ExtractionError("The AI model declined to process this document")
    monkeypatch.setattr(extraction, "call_model", call_model)
    return calls


def loinc_available():
    try:
        return loinc.lookup("718-7") is not None
    except loinc.LoincUnavailable:
        return False


needs_loinc = pytest.mark.skipif(not loinc_available(), reason="LOINC mapping project not available")
needs_validator = pytest.mark.skipif(not fhir_validation.validator_available(), reason="HL7 validator not installed")


def upload(client, headers, content: bytes, name="note.txt", **form):
    return client.post("/api/documents", headers=headers, files={"file": (name, content)}, data=form)


def first_patient(client, headers):
    return client.get("/api/patients?q=ramesh", headers=headers).json()["items"][0]


def doctor_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["doctor_id"]


def review(client, headers, doc, **changes):
    return client.put(f"/api/documents/{doc['id']}", headers=headers, json=changes)


def accept_all(data):
    """Staff accepts every low-confidence suggestion."""
    def walk(node):
        if isinstance(node, dict):
            if "review" in node and node.get("status") == "uncertain":
                node["review"] = "accepted"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return data


# 1. prescription extraction
def test_prescription_extraction(client, fake_ai):
    hims = login(client, "hims")
    created = upload(client, hims, b"PRESCRIPTION\nRx Metformin 500 mg 1-0-1 x 30 days")
    assert created.status_code == 201, created.text
    doc = client.get(f"/api/documents/{created.json()['id']}", headers=hims).json()
    assert doc["extraction_status"] == "completed" and doc["document_type"] == "prescription"
    assert doc["text"].startswith("PRESCRIPTION")
    assert fake_ai[0][0]["type"] == "text"  # TXT is sent as text; PDFs/images go as document/image blocks

    meds = doc["data"]["medications"]
    assert meds[0]["name"] == {"value": "Metformin 500 mg", "confidence": 0.95, "evidence": "as printed",
                               "source": "ai", "status": "extracted", "review": "pending"}
    assert meds[1]["name"]["status"] == "uncertain" and meds[1]["route"]["status"] == "missing"
    assert doc["review_counts"]["uncertain_pending"] == 3
    assert "Link the document to a registered patient" in doc["requirements"]
    assert any("low-confidence" in r for r in doc["requirements"])


def test_file_checks_and_failed_extraction(client):
    hims = login(client, "hims")
    assert upload(client, hims, b"MZ...", name="virus.exe").status_code == 415
    assert upload(client, hims, b"not a pdf", name="scan.pdf").status_code == 415
    assert client.get("/api/documents", headers=login(client, "reception")).status_code == 403

    # When the model fails the document stays usable for manual entry.
    failed = client.get(f"/api/documents/{upload(client, hims, b'unreadable').json()['id']}", headers=hims).json()
    assert failed["extraction_status"] == "failed" and "declined" in failed["extraction_error"]
    assert failed["data"]["medications"] == [] and failed["data"]["patient"]["name"]["source"] == "manual"


# 2 + 5. laboratory report extraction and LOINC suggestions
@needs_loinc
def test_lab_report_extraction_and_loinc(client):
    lab, hims = login(client, "labtech"), login(client, "hims")
    doc = client.get(f"/api/documents/{upload(client, lab, b'LAB REPORT').json()['id']}", headers=lab).json()
    hb, plt = doc["data"]["lab_results"]
    assert hb["value"]["value"] == "10.5" and hb["reference_range"]["value"] == "12-17"
    assert hb["loinc"]["status"] == "suggested" and hb["loinc"]["code"] == "718-7"
    assert hb["loinc"]["confidence"] == 1.0 and hb["loinc"]["display"].startswith("Hemoglobin")
    assert plt["loinc"]["code"] == "777-3"
    assert any("LOINC suggestion" in r for r in doc["requirements"])  # never confirmed automatically

    url = f"/api/documents/{doc['id']}/lab-results"
    assert client.post(f"{url}/0/loinc", headers=lab, json={"action": "confirm", "code": "99999-9"}).status_code == 422
    assert client.post(f"{url}/0/loinc", headers=login(client, "dr.mehta"), json={"action": "confirm"}).status_code == 200
    rejected = client.post(f"{url}/1/loinc", headers=hims, json={"action": "reject"}).json()
    assert rejected["data"]["lab_results"][0]["loinc"]["status"] == "confirmed"
    assert rejected["data"]["lab_results"][1]["loinc"]["status"] == "rejected"
    assert not any("LOINC" in r for r in rejected["requirements"])

    # The confirmed wording is offered first next time, still as a suggestion only.
    again = client.get(f"/api/documents/{upload(client, lab, b'LAB REPORT 2').json()['id']}", headers=lab).json()
    assert again["data"]["lab_results"][0]["loinc"]["matched_term"] == "previously confirmed for this wording"
    assert again["data"]["lab_results"][0]["loinc"]["status"] == "suggested"

    suggestion = client.get("/api/terminology/loinc/suggest", params={"q": "S. Creatinine"}, headers=lab).json()
    assert suggestion["code"] == "2160-0" and suggestion["confidence"] < 1
    assert client.get("/api/terminology/loinc/2093-3", headers=lab).json()["display"].startswith("Cholesterol")


# 3. manual correction keeps provenance honest
def test_manual_correction(client):
    hims = login(client, "hims")
    doc = client.get(f"/api/documents/{upload(client, hims, b'PRESCRIPTION').json()['id']}", headers=hims).json()
    data = doc["data"]
    data["medications"][1]["name"]["value"] = "Amlodipine 5 mg tablet"  # corrected, though still labelled "ai"
    data["medications"][1]["frequency"]["review"] = "rejected"
    data["medications"][0]["name"]["review"] = "accepted"
    data["allergies"].append({"substance": {"value": "Sulfa drugs"}, "reaction": {"value": None}})
    saved = review(client, hims, doc, data=data, patient_id=first_patient(client, hims)["id"]).json()
    meds = saved["data"]["medications"]
    assert meds[1]["name"]["source"] == "manual" and meds[1]["name"]["review"] == "edited"
    assert meds[1]["name"]["confidence"] is None and meds[1]["name"]["status"] == "extracted"
    assert meds[1]["frequency"]["status"] == "missing"  # rejected suggestions count as absent
    assert meds[0]["name"] == {**meds[0]["name"], "source": "ai", "review": "accepted"}
    assert saved["data"]["allergies"][0]["substance"]["source"] == "manual"
    assert saved["review_counts"]["manual"] == 2 and saved["patient"]["full_name"] == "Ramesh Kumar"


# 4 + 6 + 7. human approval, FHIR resources and Bundle
def test_human_approval_generates_fhir(client):
    hims, doctor, lab = login(client, "hims"), login(client, "dr.mehta"), login(client, "labtech")
    doc = client.get(f"/api/documents/{upload(client, hims, b'PRESCRIPTION').json()['id']}", headers=hims).json()

    blocked = client.post(f"/api/documents/{doc['id']}/submit", headers=hims)
    assert blocked.status_code == 422 and len(blocked.json()["detail"]["requirements"]) == 3

    patient = first_patient(client, hims)
    doc = review(client, hims, doc, patient_id=patient["id"], author_doctor_id=doctor_id(client, doctor),
                 data=accept_all(doc["data"])).json()
    assert doc["requirements"] == [] and doc["warnings"] == []
    assert client.post(f"/api/documents/{doc['id']}/submit", headers=hims).json()["status"] == "pending_approval"
    assert review(client, hims, doc, document_type="other").status_code == 409  # locked while awaiting approval
    assert client.post(f"/api/documents/{doc['id']}/approve", headers=lab).status_code == 403
    approved = client.post(f"/api/documents/{doc['id']}/approve", headers=doctor).json()
    assert approved["status"] == "approved" and approved["approved_by_name"] == "Dr. Anil Mehta"

    record = client.get(f"/api/fhir/records/{approved['fhir_record_id']}", headers=hims).json()
    assert record["record_type"] == "PrescriptionRecord" and record["validation_status"] == "not_validated"
    assert record["hip_eligible"] is False and record["prechecks"] == []
    bundle = record["bundle"]
    assert bundle["type"] == "document" and bundle["meta"]["versionId"] == "1"
    resources = [e["resource"] for e in bundle["entry"]]
    composition = resources[0]
    assert composition["meta"]["profile"] == ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/PrescriptionRecord"]
    assert {r["resourceType"] for r in resources} == {"Composition", "Patient", "Practitioner", "PractitionerRole",
                                                      "Organization", "MedicationRequest", "Binary"}
    assert [e["type"] for e in composition["section"][0]["entry"]] == ["MedicationRequest", "MedicationRequest", "Binary"]
    patient_res = next(r for r in resources if r["resourceType"] == "Patient")
    assert patient_res["identifier"][0]["value"] == patient["patient_code"] and len(patient_res["identifier"]) == 1
    metformin, amlodipine = [r for r in resources if r["resourceType"] == "MedicationRequest"]
    dosage = metformin["dosageInstruction"][0]
    assert dosage["timing"]["repeat"]["frequency"] == 2 and dosage["timing"]["repeat"]["boundsDuration"]["value"] == 30
    assert dosage["route"]["coding"][0]["code"] == "26643006" and metformin["dispenseRequest"]["quantity"]["value"] == 60
    assert "route" not in amlodipine["dosageInstruction"][0]  # not documented, so not invented
    assert amlodipine["dosageInstruction"][0]["timing"]["repeat"]["boundsDuration"]["value"] == 7

    assert review(client, hims, approved, document_type="other").status_code == 409
    regenerated = client.post(f"/api/fhir/records/{record['id']}/regenerate", headers=hims).json()
    assert regenerated["version"] == 2 and regenerated["bundle"]["entry"][0]["resource"]["id"] == composition["id"]
    download = client.get(f"/api/fhir/records/{record['id']}/download", headers=doctor)
    assert download.headers["content-type"].startswith("application/fhir+json")
    assert "attachment" in download.headers["content-disposition"]
    assert client.get("/api/fhir/records", headers=lab).status_code == 403


def test_rejection_returns_to_review(client):
    hims, doctor = login(client, "hims"), login(client, "dr.mehta")
    doc = client.get(f"/api/documents/{upload(client, hims, b'PRESCRIPTION').json()['id']}", headers=hims).json()
    doc = review(client, hims, doc, patient_id=first_patient(client, hims)["id"], author_doctor_id=doctor_id(client, doctor),
                 data=accept_all(doc["data"])).json()
    client.post(f"/api/documents/{doc['id']}/submit", headers=hims)
    rejected = client.post(f"/api/documents/{doc['id']}/reject", headers=doctor, json={"reason": "Check dose"}).json()
    assert rejected["status"] == "rejected" and rejected["status_reason"] == "Check dose"
    assert review(client, hims, rejected, document_type="prescription").json()["status"] == "draft"


# 9. missing required fields
@needs_loinc
def test_missing_required_fields(client):
    lab = login(client, "labtech")
    doc = client.get(f"/api/documents/{upload(client, lab, b'LAB REPORT').json()['id']}", headers=lab).json()
    assert "Enter the report conclusion (required by the ABDM DiagnosticReport profile)" in doc["requirements"]
    doc = review(client, lab, doc, document_type="discharge_summary").json()
    assert "Enter the admission date as YYYY-MM-DD" in doc["requirements"]

    # The engine still reports what a bundle lacks when the source data is incomplete.
    from app.database import SessionLocal
    from app.fhir import ClinicalContent, ReportData, ResultData, build_bundle
    from app.models import Doctor, Hospital, Patient
    with SessionLocal() as db:
        content = ClinicalContent(record_type="DiagnosticReportRecord", key="t", title="Lab", date="2026-11-01T10:00:00",
                                  reports=[ReportData(name="CBC", results=[ResultData(name="Hb", value="10")])])
        bundle = build_bundle(content, db.get(Patient, 1), db.get(Hospital, 1), db.get(Doctor, 1))
        op = build_bundle(ClinicalContent(record_type="OPConsultRecord", key="o", title="OP", date="2026-11-01T10:00:00",
                                          chief_complaint="Cough"), db.get(Patient, 1), db.get(Hospital, 1), db.get(Doctor, 1))
    messages = [i["message"] for i in fhir_validation.prechecks(bundle)]
    assert "DiagnosticReport (CBC) needs a conclusion" in messages
    assert "DiagnosticReport (CBC) needs an approving doctor (results interpreter)" in messages
    assert any("needs an encounter" in i["message"] for i in fhir_validation.prechecks(op))


# 10. invalid resource references
def test_invalid_references(client):
    hims = login(client, "hims")
    rx = client.post("/api/fhir/records", headers=hims, json={"source_type": "prescription", "source_id": 999})
    assert rx.status_code == 404
    from app.database import SessionLocal
    from app.fhir import ClinicalContent, MedicationData, build_bundle
    from app.models import Doctor, Hospital, Patient
    with SessionLocal() as db:
        bundle = build_bundle(ClinicalContent(record_type="PrescriptionRecord", key="r", title="Rx", date="2026-11-01T10:00:00",
                                              medications=[MedicationData(name="Paracetamol", dosage="650 mg")]),
                              db.get(Patient, 1), db.get(Hospital, 1), db.get(Doctor, 1))
    assert fhir_validation.prechecks(bundle) == []
    broken = copy.deepcopy(bundle)
    broken["entry"] = [e for e in broken["entry"] if e["resource"]["resourceType"] != "Patient"]
    issues = fhir_validation.prechecks(broken)
    assert issues and all(i["category"] == "reference" for i in issues)
    assert "MedicationRequest (Paracetamol) refers to urn:uuid:" in next(i["message"] for i in issues
                                                                         if "MedicationRequest" in i["message"])


def test_phase2_records_become_fhir_records(client):
    """Approved consultations/prescriptions/lab reports reuse the same engine."""
    from tests.test_clinical import MEDICINES, start_consultation
    doctor, hims = login(client, "dr.mehta"), login(client, "hims")
    consult = start_consultation(client, doctor, first_patient(client, doctor)["id"])
    client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={
        "chief_complaint": "Thirst", "diagnoses": [{"display": "Diabetes", "code": "E11.9"}, {"display": "Odd", "code": "Z99.99"}]})
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    assert client.post("/api/fhir/records", headers=hims, json={"source_type": "prescription", "source_id": rx["id"]}).status_code == 409
    client.post(f"/api/consultations/{consult['id']}/finalize", headers=doctor)
    record = client.post("/api/fhir/records", headers=hims, json={"source_type": "consultation", "source_id": consult["id"]}).json()
    assert record["record_type"] == "OPConsultRecord"
    conditions = [e["resource"] for e in record["bundle"]["entry"] if e["resource"]["resourceType"] == "Condition"]
    assert conditions[0]["code"]["coding"][0]["display"] == "Type 2 diabetes mellitus without complications"
    assert "coding" not in conditions[1]["code"]  # unverifiable code: text only, with a warning
    assert any("Z99.99 could not be verified" in i["message"] for i in record["prechecks"])
    listing = client.get("/api/fhir/records", headers=hims).json()
    assert listing["total"] == 1 and listing["items"][0]["hip_eligible"] is False


# 8. ABDM profile validation with the official HL7 validator (about a minute per bundle)
@needs_validator
def test_hl7_profile_validation(client):
    hims, doctor = login(client, "hims"), login(client, "dr.mehta")
    doc = client.get(f"/api/documents/{upload(client, hims, b'PRESCRIPTION').json()['id']}", headers=hims).json()
    review(client, hims, doc, patient_id=first_patient(client, hims)["id"], author_doctor_id=doctor_id(client, doctor),
           data=accept_all(doc["data"]))
    client.post(f"/api/documents/{doc['id']}/submit", headers=hims)
    record_id = client.post(f"/api/documents/{doc['id']}/approve", headers=doctor).json()["fhir_record_id"]

    validated = client.post(f"/api/fhir/records/{record_id}/validate", headers=hims).json()
    errors = [i for i in validated["validation_issues"] if i["severity"] == "error"]
    assert validated["validation_status"] == "valid", errors
    assert validated["hip_eligible"] is True and "ndhm.in#6.5.0" in validated["validator"]

    # A bundle that breaks the profile is reported in plain language at the right resource.
    broken = copy.deepcopy(validated["bundle"])
    medication = next(e["resource"] for e in broken["entry"] if e["resource"]["resourceType"] == "MedicationRequest")
    del medication["requester"], medication["authoredOn"]
    status, issues, _ = fhir_validation.validate(broken)
    assert status == "invalid"
    located = [i for i in issues if i["severity"] == "error" and (i["location"] or "").startswith("MedicationRequest")]
    assert located and {i["category"] for i in located} == {"required"}


@needs_validator
@needs_loinc
def test_hl7_validation_of_lab_report_with_confirmed_loinc(client):
    lab, doctor = login(client, "labtech"), login(client, "dr.rao")
    doc = client.get(f"/api/documents/{upload(client, lab, b'LAB REPORT').json()['id']}", headers=lab).json()
    data = doc["data"]
    data["report_conclusion"] = {"value": "Mild anaemia with thrombocytosis"}  # entered by staff
    doc = review(client, lab, doc, patient_id=first_patient(client, lab)["id"], author_doctor_id=doctor_id(client, doctor),
                 data=data).json()
    for index in (0, 1):
        doc = client.post(f"/api/documents/{doc['id']}/lab-results/{index}/loinc", headers=lab, json={"action": "confirm"}).json()
    assert doc["requirements"] == [], doc["requirements"]
    client.post(f"/api/documents/{doc['id']}/submit", headers=lab)
    record_id = client.post(f"/api/documents/{doc['id']}/approve", headers=doctor).json()["fhir_record_id"]
    record = client.get(f"/api/fhir/records/{record_id}", headers=doctor).json()
    report = next(e["resource"] for e in record["bundle"]["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
    observations = [e["resource"] for e in record["bundle"]["entry"] if e["resource"]["resourceType"] == "Observation"]
    assert report["code"]["text"] == "CBC" and report["resultsInterpreter"][0]["display"] == "Dr. Sunita Rao"
    assert observations[0]["code"]["coding"][0]["code"] == "718-7"
    assert observations[0]["referenceRange"][0] == {"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 17.0, "unit": "g/dL"}}
    assert [o["interpretation"][0]["coding"][0]["code"] for o in observations] == ["L", "H"]

    validated = client.post(f"/api/fhir/records/{record_id}/validate", headers=doctor).json()
    assert validated["validation_status"] == "valid", [i for i in validated["validation_issues"] if i["severity"] == "error"]

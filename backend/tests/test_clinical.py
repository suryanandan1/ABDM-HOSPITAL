from tests.conftest import login
from tests.test_api import register

MEDICINES = [
    {"medicine_name": "Metformin 500 mg tablet", "dosage": "1 tablet", "route": "oral", "frequency": "BD",
     "duration_days": 30, "quantity": 60, "instructions": "After meals"},
    {"medicine_name": "Paracetamol 650 mg tablet", "dosage": "1 tablet", "route": "oral", "frequency": "SOS",
     "duration_days": 5, "quantity": 10},
]


def start_consultation(client, doctor, patient_id):
    response = client.post("/api/consultations", json={"patient_id": patient_id}, headers=doctor)
    assert response.status_code == 201, response.text
    return response.json()


def lab_test_id(client, headers, code):
    return next(t["id"] for t in client.get("/api/lab/tests", headers=headers).json() if t["code"] == code)


def test_complete_clinical_workflow(client):
    reception, doctor, lab, rao = (login(client, u) for u in ("reception", "dr.mehta", "labtech", "dr.rao"))

    # 1. Register a patient
    patient = register(client, reception).json()

    # 2. Create a consultation with vitals and clinical notes
    consult = start_consultation(client, doctor, patient["id"])
    assert consult["status"] == "draft" and consult["doctor"]["full_name"] == "Dr. Anil Mehta"
    saved = client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={
        "chief_complaint": "Increased thirst and frequent urination for 3 weeks",
        "history_present_illness": "Gradual onset, weight loss of 3 kg",
        "clinical_findings": "Dry mucosa, otherwise normal",
        "vitals": {"temperature": 37.1, "pulse": 88, "systolic_bp": 138, "diastolic_bp": 86, "spo2": 98, "weight": 72},
        "treatment_plan": "Start metformin, diet advice",
        "follow_up_instructions": "Review with reports",
        "follow_up_date": "2026-12-01",
        # 3. Add a diagnosis
        "diagnoses": [{"display": "Type 2 diabetes mellitus", "code": "e11.9", "verification_status": "provisional"}],
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["conditions"][0]["code"] == "E11.9"
    assert saved.json()["vitals"]["pulse"] == 88

    # 4. Create a prescription with several medicines, then review and approve it
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES})
    assert rx.status_code == 201, rx.text
    rx_id = rx.json()["id"]
    assert len(rx.json()["items"]) == 2 and rx.json()["status"] == "draft"
    assert client.post(f"/api/prescriptions/{rx_id}/approve", headers=doctor).status_code == 409  # must submit first
    assert client.post(f"/api/prescriptions/{rx_id}/submit", headers=doctor).json()["status"] == "pending_approval"
    assert client.put(f"/api/prescriptions/{rx_id}", headers=doctor, json={"items": MEDICINES}).status_code == 409
    assert client.post(f"/api/prescriptions/{rx_id}/approve", headers=rao).status_code == 403  # not the prescriber
    approved_rx = client.post(f"/api/prescriptions/{rx_id}/approve", headers=doctor).json()
    assert approved_rx["status"] == "approved" and approved_rx["approved_by_name"] == "Dr. Anil Mehta"

    # 5. Order laboratory tests
    orders = client.post("/api/lab/orders", headers=doctor, json={
        "encounter_id": consult["id"], "test_ids": [lab_test_id(client, doctor, "FBS"), lab_test_id(client, doctor, "CBC")],
        "priority": "urgent", "clinical_notes": "Suspected diabetes"})
    assert orders.status_code == 201, orders.text
    cbc = next(o for o in orders.json() if o["test"]["code"] == "CBC")
    assert cbc["status"] == "ordered" and cbc["sample_status"] == "pending"

    # Sample collection
    assert client.put(f"/api/lab/orders/{cbc['id']}/report", headers=lab, json={"results": []}).status_code == 409
    sample = client.post(f"/api/lab/orders/{cbc['id']}/sample", headers=lab, json={"sample_barcode": "B-1001"}).json()
    assert sample["status"] == "sample_collected" and sample["sample_collected_by_name"] == "Rahul Verma"

    # 6. Enter results (partial draft, then complete); abnormal values are flagged
    partial = client.put(f"/api/lab/orders/{cbc['id']}/report", headers=lab,
                         json={"results": [{"code": "HB", "value": "10.5"}]})
    assert partial.json()["report"]["status"] == "draft"
    assert client.post(f"/api/lab/orders/{cbc['id']}/report/submit", headers=lab).status_code == 422  # incomplete
    bad_code = client.put(f"/api/lab/orders/{cbc['id']}/report", headers=lab, json={"results": [{"code": "XYZ", "value": "1"}]})
    assert bad_code.status_code == 422
    full = client.put(f"/api/lab/orders/{cbc['id']}/report", headers=lab, json={
        "results": [{"code": "HB", "value": "10.5"}, {"code": "WBC", "value": "7.2"}, {"code": "PLT", "value": "500"}],
        "conclusion": "Mild anaemia, thrombocytosis"}).json()
    flags = {r["code"]: r["interpretation"] for r in full["report"]["results"]}
    assert flags == {"HB": "L", "WBC": "N", "PLT": "H"}
    assert full["report"]["results"][0]["unit"] == "g/dL" and full["status"] == "in_progress"

    # 7. Submit, approve (by a doctor who did not enter the results) and view the final report
    assert client.post(f"/api/lab/orders/{cbc['id']}/report/submit", headers=lab).json()["report"]["status"] == "pending_approval"
    assert client.post(f"/api/lab/orders/{cbc['id']}/report/approve", headers=lab).status_code == 403  # no lab:approve
    rejected = client.post(f"/api/lab/orders/{cbc['id']}/report/reject", headers=rao, json={"reason": "Recheck platelets"})
    assert rejected.json()["report"]["status"] == "rejected"
    client.put(f"/api/lab/orders/{cbc['id']}/report", headers=lab, json={
        "results": [{"code": "HB", "value": "10.5"}, {"code": "WBC", "value": "7.2"}, {"code": "PLT", "value": "480"}],
        "conclusion": "Mild anaemia, thrombocytosis"})
    client.post(f"/api/lab/orders/{cbc['id']}/report/submit", headers=lab)
    final = client.post(f"/api/lab/orders/{cbc['id']}/report/approve", headers=rao).json()
    assert final["status"] == "completed" and final["report"]["status"] == "approved"
    assert final["report"]["approved_by_name"] == "Dr. Sunita Rao"
    assert client.get(f"/api/lab/orders/{cbc['id']}", headers=doctor).json()["report"]["conclusion"].startswith("Mild")

    # Finalize the consultation; it is then locked
    finalized = client.post(f"/api/consultations/{consult['id']}/finalize", headers=doctor).json()
    assert finalized["status"] == "approved"
    assert client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={"doctor_notes": "x"}).status_code == 409

    # 8. Patient clinical history shows only finalized/approved records
    history = client.get(f"/api/patients/{patient['id']}/history", headers=doctor).json()
    assert [c["display"] for c in history["active_conditions"]] == ["Type 2 diabetes mellitus"]
    assert len(history["consultations"]) == 1 and len(history["prescriptions"]) == 1
    assert [o["test"]["code"] for o in history["lab_orders"]] == ["CBC"]  # FBS still pending

    # FHIR documents for approved records
    op = client.get(f"/api/fhir/consultations/{consult['id']}", headers=doctor)
    assert op.headers["content-type"].startswith("application/fhir+json")
    bundle = op.json()
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert bundle["type"] == "document" and types[0] == "Composition"
    assert {"Patient", "Practitioner", "Organization", "Encounter", "Condition", "Observation",
            "MedicationRequest", "ServiceRequest"} <= set(types)
    assert bundle["entry"][0]["resource"]["meta"]["profile"][0].endswith("OPConsultRecord")
    bp = next(e["resource"] for e in bundle["entry"] if e["resource"].get("code", {}).get("text") == "Blood pressure")
    assert [c["valueQuantity"]["value"] for c in bp["component"]] == [138, 86]
    patient_resource = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
    assert len(patient_resource["identifier"]) == 1  # unverified ABHA is not exported

    report_bundle = client.get(f"/api/fhir/lab-orders/{cbc['id']}", headers=doctor).json()
    report = next(e["resource"] for e in report_bundle["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
    assert len(report["result"]) == 3 and report["conclusion"].startswith("Mild")
    rx_bundle = client.get(f"/api/fhir/prescriptions/{rx_id}", headers=doctor).json()
    meds = [e["resource"] for e in rx_bundle["entry"] if e["resource"]["resourceType"] == "MedicationRequest"]
    assert meds[0]["dosageInstruction"][0]["timing"]["repeat"]["frequency"] == 2
    assert meds[1]["dosageInstruction"][0]["asNeededBoolean"] is True


def test_consultation_rules(client):
    doctor, rao, reception = login(client, "dr.mehta"), login(client, "dr.rao"), login(client, "reception")
    patient_id = client.get("/api/patients", headers=reception).json()["items"][0]["id"]
    admin = login(client, "admin", "Admin1234")

    assert client.post("/api/consultations", json={"patient_id": patient_id}, headers=reception).status_code == 403
    no_profile = client.post("/api/consultations", json={"patient_id": patient_id}, headers=admin)
    assert no_profile.status_code == 403 and "doctor profile" in no_profile.json()["detail"]
    assert client.get("/api/consultations", headers=reception).status_code == 403  # receptionists see no clinical data

    consult = start_consultation(client, doctor, patient_id)
    assert client.put(f"/api/consultations/{consult['id']}", headers=rao, json={"doctor_notes": "x"}).status_code == 403
    assert client.post(f"/api/consultations/{consult['id']}/finalize", headers=doctor).status_code == 422  # no complaint
    vitals = client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={"vitals": {"pulse": 900}})
    assert vitals.status_code == 422
    bad_icd = client.put(f"/api/consultations/{consult['id']}", headers=doctor,
                         json={"diagnoses": [{"display": "Fever", "code": "fever"}]})
    assert bad_icd.status_code == 422
    assert client.get(f"/api/fhir/consultations/{consult['id']}", headers=doctor).status_code == 409  # draft

    cancelled = client.post(f"/api/consultations/{consult['id']}/cancel", headers=doctor, json={"reason": "Wrong patient"})
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/api/prescriptions", headers=doctor,
                       json={"encounter_id": consult["id"], "items": MEDICINES}).status_code == 409


def test_condition_can_be_resolved_after_finalize(client):
    doctor = login(client, "dr.mehta")
    patient_id = client.get("/api/patients", headers=doctor).json()["items"][0]["id"]
    consult = start_consultation(client, doctor, patient_id)
    saved = client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={
        "chief_complaint": "Fever", "diagnoses": [{"display": "Viral fever", "code": "B34.9"}]}).json()
    client.post(f"/api/consultations/{consult['id']}/finalize", headers=doctor)
    condition_id = saved["conditions"][0]["id"]
    resolved = client.patch(f"/api/conditions/{condition_id}", headers=doctor, json={"clinical_status": "resolved"})
    assert resolved.json()["clinical_status"] == "resolved"
    assert client.get(f"/api/patients/{patient_id}/history", headers=doctor).json()["active_conditions"] == []


def test_prescription_validation_and_rejection(client):
    doctor = login(client, "dr.mehta")
    patient_id = client.get("/api/patients", headers=doctor).json()["items"][0]["id"]
    consult = start_consultation(client, doctor, patient_id)
    assert client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": []}).status_code == 422
    bad = {**MEDICINES[0], "route": "teleport", "frequency": "XYZ", "duration_days": 0}
    assert client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": [bad]}).status_code == 422

    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    client.post(f"/api/prescriptions/{rx['id']}/submit", headers=doctor)
    rejected = client.post(f"/api/prescriptions/{rx['id']}/reject", headers=doctor, json={"reason": "Dose to be revised"})
    assert rejected.json()["status"] == "rejected"
    edited = client.put(f"/api/prescriptions/{rx['id']}", headers=doctor, json={"items": MEDICINES[:1]})
    assert edited.json()["status"] == "draft" and len(edited.json()["items"]) == 1
    cancelled = client.post(f"/api/prescriptions/{rx['id']}/cancel", headers=doctor, json={"reason": "Not needed"})
    assert cancelled.json()["status"] == "cancelled"
    listing = client.get(f"/api/prescriptions?patient_id={patient_id}&status=cancelled", headers=doctor).json()
    assert listing["total"] == 1


def test_lab_catalogue_and_sample_rejection(client, admin):
    lab, doctor = login(client, "labtech"), login(client, "dr.mehta")
    test = {"code": "tsh", "name": "Thyroid Stimulating Hormone", "sample_type": "Serum", "loinc_code": "3016-3",
            "parameters": [{"code": "tsh", "name": "TSH", "unit": "uIU/mL", "ref_low": 0.4, "ref_high": 4.0}]}
    created = client.post("/api/lab/tests", headers=lab, json=test)
    assert created.status_code == 201 and created.json()["code"] == "TSH"
    assert client.post("/api/lab/tests", headers=lab, json=test).status_code == 409
    assert client.post("/api/lab/tests", headers=doctor, json={**test, "code": "T2"}).status_code == 403
    bad_range = {**test, "code": "T3", "parameters": [{**test["parameters"][0], "ref_low": 5, "ref_high": 1}]}
    assert client.post("/api/lab/tests", headers=lab, json=bad_range).status_code == 422

    patient_id = client.get("/api/patients", headers=doctor).json()["items"][0]["id"]
    consult = start_consultation(client, doctor, patient_id)
    order = client.post("/api/lab/orders", headers=doctor,
                        json={"encounter_id": consult["id"], "test_ids": [created.json()["id"]]}).json()[0]
    client.post(f"/api/lab/orders/{order['id']}/sample", headers=lab, json={})
    rejected = client.post(f"/api/lab/orders/{order['id']}/sample/reject", headers=lab, json={"reason": "Haemolysed"})
    assert rejected.json()["status"] == "ordered" and rejected.json()["sample_status"] == "rejected"
    recollected = client.post(f"/api/lab/orders/{order['id']}/sample", headers=lab, json={}).json()
    assert recollected["sample_status"] == "collected" and recollected["sample_barcode"].startswith("S")

    # Text results keep the technician's flag
    urine = client.post("/api/lab/orders", headers=doctor, json={
        "encounter_id": consult["id"], "test_ids": [lab_test_id(client, doctor, "URINE-RM")]}).json()[0]
    client.post(f"/api/lab/orders/{urine['id']}/sample", headers=lab, json={})
    saved = client.put(f"/api/lab/orders/{urine['id']}/report", headers=lab, json={"results": [
        {"code": "PROT", "value": "Trace", "interpretation": "A"}, {"code": "SUGAR", "value": "Negative", "interpretation": "N"}]})
    assert [r["interpretation"] for r in saved.json()["report"]["results"]] == ["A", "N"]

    # Deactivated tests cannot be ordered; cancelled orders cannot be worked on
    client.put(f"/api/lab/tests/{created.json()['id']}", headers=lab, json={**test, "is_active": False})
    again = client.post("/api/lab/orders", headers=doctor, json={"encounter_id": consult["id"], "test_ids": [created.json()["id"]]})
    assert again.status_code == 409
    fbs = client.post("/api/lab/orders", headers=doctor, json={
        "encounter_id": consult["id"], "test_ids": [lab_test_id(client, doctor, "FBS")]}).json()[0]
    client.post(f"/api/lab/orders/{fbs['id']}/cancel", headers=doctor, json={"reason": "Patient declined"})
    assert client.post(f"/api/lab/orders/{fbs['id']}/sample", headers=lab, json={}).status_code == 409
    assert client.get("/api/lab/orders?status=cancelled", headers=lab).json()["total"] == 1


def test_lab_report_requires_second_person(client, admin):
    """Whoever entered the results cannot approve them; approval also needs a doctor profile."""
    doctor, lab = login(client, "dr.mehta"), login(client, "labtech")
    patient_id = client.get("/api/patients", headers=doctor).json()["items"][0]["id"]
    consult = start_consultation(client, doctor, patient_id)
    order = client.post("/api/lab/orders", headers=doctor, json={
        "encounter_id": consult["id"], "test_ids": [lab_test_id(client, doctor, "FBS")]}).json()[0]
    client.post(f"/api/lab/orders/{order['id']}/sample", headers=admin, json={})
    client.put(f"/api/lab/orders/{order['id']}/report", headers=admin,
               json={"results": [{"code": "GLU", "value": "140"}], "conclusion": "Raised fasting glucose"})
    client.post(f"/api/lab/orders/{order['id']}/report/submit", headers=admin)

    own = client.post(f"/api/lab/orders/{order['id']}/report/approve", headers=admin)
    assert own.status_code == 403 and "someone other" in own.json()["detail"]
    assert client.post(f"/api/lab/orders/{order['id']}/report/approve", headers=lab).status_code == 403
    approved = client.post(f"/api/lab/orders/{order['id']}/report/approve", headers=doctor).json()
    assert approved["status"] == "completed" and approved["report"]["results"][0]["interpretation"] == "H"


def test_terminology_and_doctor_link(client, admin):
    doctor = login(client, "dr.mehta")
    terms = client.get("/api/terminology", headers=doctor).json()
    assert {"oral", "iv"} <= {r["key"] for r in terms["routes"]}
    assert "BD" in {f["key"] for f in terms["frequencies"]}
    assert client.get("/api/auth/me", headers=doctor).json()["doctor_id"] is not None
    assert client.get("/api/auth/me", headers=admin).json()["doctor_id"] is None

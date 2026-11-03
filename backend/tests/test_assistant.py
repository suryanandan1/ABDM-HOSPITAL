"""Phase 6: AI assistant (scoping, traceability, safety) and security hardening."""

import logging

import pytest

from app import assistant
from app.config import Settings
from tests.conftest import login
from tests.test_clinical import MEDICINES, lab_test_id, start_consultation

PROMPTS = []


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    """Answers cite one real record, one unknown record and one invented date, to exercise the checks."""
    PROMPTS.clear()

    def call_model(system, content, output_format):
        PROMPTS.append(content[0]["text"])
        import json
        records = json.loads(content[0]["text"])["records"]
        real = records[0]
        good = assistant.Statement(text="Documented", kind="documented_fact", source_ids=[real["id"]],
                                   dates=[real["date"][:10], "1999-01-01"])
        bad = assistant.Statement(text="Invented", kind="ai_interpretation", source_ids=["CONS-999"], dates=[])
        if output_format is assistant.Answer:
            if "nothing" in content[0]["text"]:
                return assistant.Answer(statements=[bad], information_unavailable=False, unavailable_reason=None)
            return assistant.Answer(statements=[good, bad], information_unavailable=False, unavailable_reason=None)
        return assistant.Summary(consultations=[good], conditions=[bad], medications=[], laboratory=[], observations=[],
                                 external_records=[], missing_information=["No allergy information recorded"])
    monkeypatch.setattr(assistant, "call_model", call_model)


def patient_with_history(client):
    doctor, lab, rao = login(client, "dr.mehta"), login(client, "labtech"), login(client, "dr.rao")
    patient = client.get("/api/patients?q=ramesh", headers=doctor).json()["items"][0]
    consult = start_consultation(client, doctor, patient["id"])
    client.put(f"/api/consultations/{consult['id']}", headers=doctor, json={
        "chief_complaint": "Thirst", "diagnoses": [{"display": "Type 2 diabetes mellitus", "code": "E11.9"}]})
    rx = client.post("/api/prescriptions", headers=doctor, json={"encounter_id": consult["id"], "items": MEDICINES}).json()
    client.post(f"/api/prescriptions/{rx['id']}/submit", headers=doctor)
    client.post(f"/api/prescriptions/{rx['id']}/approve", headers=doctor)
    for value in ("140", "118"):  # the same test twice: a comparable trend
        order = client.post("/api/lab/orders", headers=doctor, json={"encounter_id": consult["id"],
                                                                     "test_ids": [lab_test_id(client, doctor, "FBS")]}).json()[0]
        client.post(f"/api/lab/orders/{order['id']}/sample", headers=lab, json={})
        client.put(f"/api/lab/orders/{order['id']}/report", headers=lab,
                   json={"results": [{"code": "GLU", "value": value}], "conclusion": "Fasting glucose"})
        client.post(f"/api/lab/orders/{order['id']}/report/submit", headers=lab)
        client.post(f"/api/lab/orders/{order['id']}/report/approve", headers=rao)
    client.post(f"/api/consultations/{consult['id']}/finalize", headers=doctor)
    return patient, consult, rx


def test_overview_is_deterministic_cited_and_scoped(client):
    patient, consult, rx = patient_with_history(client)
    doctor = login(client, "dr.mehta")
    overview = client.get(f"/api/assistant/patients/{patient['id']}/overview", headers=doctor).json()
    ids = [t["id"] for t in overview["timeline"]]
    assert {f"CONS-{consult['id']}", f"RX-{rx['id']}"} <= set(ids) and sum(i.startswith("LAB-") for i in ids) == 2
    briefing = overview["briefing"]
    assert briefing["active_conditions"][0]["text"] == "Type 2 diabetes mellitus"
    assert briefing["active_conditions"][0]["certainty"] == "provisional"  # never shown as confirmed
    assert briefing["recent_lab_results"][0]["abnormal"] is True  # 140 mg/dL is above range: listed first
    assert briefing["previous_prescriptions"][0]["source_id"] == f"RX-{rx['id']}"
    assert any("ABHA not verified" in m for m in briefing["missing_information"])
    [trend] = overview["lab_trends"]
    assert trend["comparable"] and trend["change"] == -22.0 and trend["points"][0]["unit"] == "mg/dL"
    assert all(i in overview["sources"] for i in ids)

    # Scope follows permissions: lab technicians have no clinical access; reception none at all.
    assert client.get(f"/api/assistant/patients/{patient['id']}/overview", headers=login(client, "labtech")).status_code == 403
    assert client.get(f"/api/assistant/patients/{patient['id']}/overview", headers=login(client, "reception")).status_code == 403
    hims = client.get(f"/api/assistant/patients/{patient['id']}/overview", headers=login(client, "hims")).json()
    assert "external records received under consent" not in hims["briefing"]["scope"]


def test_summary_and_answers_are_traceable(client):
    patient, _, _ = patient_with_history(client)
    doctor = login(client, "dr.mehta")
    summary = client.post(f"/api/assistant/patients/{patient['id']}/summary", headers=doctor).json()
    assert summary["removed_unsupported"] == 1 and summary["conditions"] == []
    statement = summary["consultations"][0]
    assert statement["kind"] == "documented_fact" and statement["source_ids"][0] in summary["sources"]
    assert "1999-01-01" not in statement["dates"] and len(statement["dates"]) == 1  # dates must come from the source
    assert summary["missing_information"] == ["No allergy information recorded"]
    assert "Ramesh" not in PROMPTS[0] and "9876543210" not in PROMPTS[0]  # no direct identifiers sent to the AI

    answer = client.post(f"/api/assistant/patients/{patient['id']}/ask", headers=doctor,
                         json={"question": "What medicines were previously prescribed?"}).json()
    assert len(answer["statements"]) == 1 and answer["removed_unsupported"] == 1
    unsupported = client.post(f"/api/assistant/patients/{patient['id']}/ask", headers=doctor,
                              json={"question": "Tell me nothing"}).json()
    assert unsupported["information_unavailable"] is True and unsupported["statements"] == []
    logs = client.get("/api/audit-logs?action=assistant.question", headers=login(client, "admin", "Admin1234")).json()
    assert logs["total"] == 2 and "medicines" not in str(logs["items"])  # the question is not stored


def test_no_records_means_unavailable(client):
    doctor = login(client, "dr.mehta")
    sita = client.get("/api/patients?q=sita", headers=doctor).json()["items"][0]
    answer = client.post(f"/api/assistant/patients/{sita['id']}/ask", headers=doctor,
                         json={"question": "Latest consultation?"}).json()
    assert answer["information_unavailable"] and "no records" in answer["unavailable_reason"]
    assert client.post(f"/api/assistant/patients/{sita['id']}/summary", headers=doctor).status_code == 422


# ---------- security ----------

def test_login_throttling(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "labtech", "password": "wrong"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "labtech", "password": "Demo@1234"})
    assert blocked.status_code == 429
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401


def test_security_headers(client):
    response = client.get("/api/auth/me", headers=login(client, "dr.mehta"))
    assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_access_log_redaction():
    from app.main import RedactAccessLog
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', None, None)
    record.args = ("127.0.0.1", "GET", "/api/patients?q=Ramesh%20Kumar", "1.1", 200)
    RedactAccessLog().filter(record)
    assert record.args[2] == "/api/patients"
    record.args = ("127.0.0.1", "POST", "/api/hiu/data-push/SECRET-TOKEN", "1.1", 202)
    RedactAccessLog().filter(record)
    assert record.args[2] == "/api/hiu/data-push/***"


def test_example_secret_is_refused():
    with pytest.raises(ValueError):
        Settings(jwt_secret="change-me-to-a-long-random-secret-value-1234567890")


def test_audit_log_keeps_no_patient_details(client):
    reception, admin = login(client, "reception"), login(client, "admin", "Admin1234")
    patient = client.get("/api/patients?q=sita", headers=reception).json()["items"][0]
    client.patch(f"/api/patients/{patient['id']}", headers=reception, json={"full_name": "Sita Devi Kulkarni", "city": "Nashik"})
    logs = str(client.get("/api/audit-logs?action=patient.update", headers=admin).json())
    assert "Kulkarni" not in logs and "Nashik" not in logs and "full_name" in logs

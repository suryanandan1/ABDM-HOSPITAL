"""Simulation (ABDM_MODE=simulation) of the consent manager and the patient's consent decision for the HIU workflow.

Where the records come from:
- Several hospitals in this database: the consent manager looks for care contexts that the *other* hospitals have
  linked to the patient's ABHA address (e.g. surya@abdm). Each such hospital becomes a HIP of the exchange: it is
  sent the consent artefact, receives the health information request and encrypts and pushes its own validated
  records to the requesting hospital, all through the same HIP code that handles real ABDM requests.
- A single hospital: an external HIP ("Sunrise Clinic (SIMULATED)") sends synthetic records, built with the FHIR
  engine, filtered to the consent's scope and encrypted exactly as a real HIP would.
Calls arrive through the shared gateway client; callbacks go through app.hip.simulator's queue (signed and
verified like real ones). The consent manager's state is in memory and starts empty after a restart.
"""

import hashlib
import json
import uuid
from datetime import datetime

from app.fhir import (ClinicalContent, ConditionData, Coding, EncounterData, MedicationData, ReportData, ResultData,
                      build_bundle, LOINC)
from app.hip import crypto, gateway
from app.models import Doctor, Hospital, Patient

EXTERNAL_HIP = "SIM_EXT_HIP_01"
consent_requests: dict[str, dict] = {}  # consent request id -> {"consent": body, "status", "artefacts", "hiu_id"}
artefacts: dict[str, dict] = {}  # artefact id -> consentDetail
local_hips: dict[str, str] = {}  # artefact id -> HIP ID of the hospital in this database that holds the records
pushes: list[dict] = []  # what the simulated external HIP pushed to the HIU (metadata only)

HI_TYPE_OF = {"OPConsultRecord": "OPConsultation", "PrescriptionRecord": "Prescription",
              "DiagnosticReportRecord": "DiagnosticReport", "DischargeSummaryRecord": "DischargeSummary"}


class NoSources(ValueError):
    pass


def reset() -> None:
    consent_requests.clear(), artefacts.clear(), local_hips.clear(), pushes.clear()


def queue(kind: str, body: dict, hiu_id: str | None = None) -> None:
    """A consent manager callback to an HIU."""
    from app.hip import simulator
    headers = simulator.callback_headers()
    headers["X-HIU-ID"] = hiu_id or gateway.hiu_id()
    simulator._callbacks.append((kind, headers, body))


def queue_hip(kind: str, body: dict, hip_id: str) -> None:
    """A consent manager request to a HIP (consent notification, health information request)."""
    from app.hip import simulator
    simulator._callbacks.append((kind, simulator.callback_headers(hip_id), body))


def receive(path: str, body: dict, headers: dict) -> tuple[int, dict | None]:
    request_id, hiu_id = headers.get("REQUEST-ID"), headers.get("X-HIU-ID")
    if path == gateway.CONSENT_INIT_PATH:
        consent_id = str(uuid.uuid4())
        consent_requests[consent_id] = {"consent": body["consent"], "status": "REQUESTED", "artefacts": [], "hiu_id": hiu_id}
        queue("hiu_on_init", {"consentRequest": {"id": consent_id}, "error": None, "response": {"requestId": request_id}},
              hiu_id)
    elif path == gateway.CONSENT_STATUS_PATH:
        item = consent_requests.get(body.get("consentRequestId"))
        queue("hiu_on_status", {"consentRequest": {"id": body.get("consentRequestId"), "status": item["status"] if item else None},
                                "error": None if item else {"code": "ABDM-1039", "message": "Invalid Consent request id"},
                                "response": {"requestId": request_id}}, hiu_id)
    elif path == gateway.CONSENT_FETCH_PATH:
        detail = artefacts.get(body.get("consentId"))
        if detail and detail["hiu"]["id"] != hiu_id:
            detail = None  # an artefact is only given to the HIU it was granted to
        reply = {"consent": {"status": "GRANTED", "consentDetail": detail, "signature": "simulated"} if detail else None,
                 "error": None if detail else {"code": "ABDM-1080", "message": "Invalid Consent artefact id"},
                 "response": {"requestId": request_id}}
        queue("hiu_on_fetch", reply, hiu_id)
    elif path == gateway.HI_REQUEST_PATH:
        transaction_id, artefact_id = str(uuid.uuid4()), body["hiRequest"]["consent"]["id"]
        queue("hiu_on_request", {"hiRequest": {"transactionId": transaction_id, "sessionStatus": "REQUESTED"},
                                 "error": None, "response": {"requestId": request_id}}, hiu_id)
        if artefact_id in local_hips:  # forward to the hospital holding the records; it pushes to the HIU itself
            queue_hip("health_information", {"transactionId": transaction_id, "hiRequest": body["hiRequest"]},
                      local_hips[artefact_id])
        else:
            queue("hiu_push", {"transactionId": transaction_id, "hiRequest": body["hiRequest"]}, hiu_id)
    return 202, None


def local_sources(consent: dict, hi_types: list[str]) -> list[tuple[str, str, list[dict]]] | None:
    """Care contexts that other hospitals of this database linked to the patient's ABHA address, within the
    requested types and dates: [(HIP ID, hospital name, careContexts)]. None when this database has one hospital."""
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.hip.service import hospital_for, parse_time
    from app.models import CareContext
    address = (consent["patient"]["id"] or "").lower()
    date_range = consent["permission"]["dateRange"]
    start, end = parse_time(date_range["from"]), parse_time(date_range["to"])
    with SessionLocal() as db:
        if db.scalar(select(func.count(Hospital.id))) < 2:
            return None
        requester = hospital_for(db, consent["hiu"]["id"], "hiu")
        rows = db.execute(select(CareContext, Patient).join(Patient, Patient.id == CareContext.patient_id).where(
            CareContext.link_status == "linked", func.lower(CareContext.linked_abha_address) == address)
            .order_by(CareContext.hospital_id, CareContext.id)).all()
        sources: dict[str, tuple[str, list[dict]]] = {}
        for context, patient in rows:
            if requester is not None and context.hospital_id == requester.id:
                continue  # the HIU's own records are already in its own system
            if context.hi_type not in hi_types or not start <= context.record_date <= end:
                continue
            hospital = db.get(Hospital, context.hospital_id)
            hip_id = gateway.hip_id(hospital)
            owner = hospital_for(db, hip_id, "hip")
            if owner is None or owner.id != hospital.id:
                continue  # this hospital has no HIP ID of its own, so ABDM could not route to it
            sources.setdefault(hip_id, (hospital.name, []))[1].append(
                {"patientReference": patient.patient_code, "careContextReference": context.reference})
        return [(hip_id, name, contexts) for hip_id, (name, contexts) in sources.items()]


def decide(consent_request_id: str, grant: bool, hi_types: list[str] | None = None, reason: str | None = None) -> dict:
    """The patient's decision in their ABHA app (simulated). A grant may narrow the requested record types.
    A grant creates one consent artefact per HIP holding matching records, as ABDM's consent manager does."""
    item = consent_requests.get(consent_request_id)
    if item is None:
        raise ValueError("Unknown consent request (the simulator restarts empty)")
    consent = item["consent"]
    if not grant:
        item["status"] = "DENIED"
        notification = {"consentRequestId": consent_request_id, "status": "DENIED", "reason": reason, "consentArtefacts": []}
    else:
        granted_types = sorted(set(hi_types or consent["hiTypes"]) & set(consent["hiTypes"]))
        sources = local_sources(consent, granted_types)
        if sources is None:  # a single hospital: the synthetic external provider
            sources = [(EXTERNAL_HIP, "Sunrise Clinic (SIMULATED)",
                        [{"patientReference": "SUN-000777", "careContextReference": f"SUN-CC-{i}"} for i in range(1, 6)])]
        elif not sources:
            raise NoSources(f"No other hospital has records linked to {consent['patient']['id']} of the requested "
                            "types and dates. Link them first on that hospital's ABDM HIP page.")
        item["status"] = "GRANTED"
        ids = []
        for hip_id, _, contexts in sources:
            artefact_id = str(uuid.uuid4())
            detail = {
                "consentId": artefact_id, "schemaVersion": "v3", "createdAt": gateway.timestamp(),
                "hip": {"id": hip_id}, "hiu": consent["hiu"], "patient": consent["patient"], "hiTypes": granted_types,
                "purpose": consent["purpose"], "requester": consent["requester"], "permission": consent["permission"],
                "careContexts": contexts, "consentManager": {"id": "sbx"}, "lastUpdated": gateway.timestamp()}
            artefacts[artefact_id] = detail
            item["artefacts"].append(artefact_id)
            ids.append({"id": artefact_id})
            if hip_id != EXTERNAL_HIP:  # the HIP learns of the consent before the HIU asks it for data
                local_hips[artefact_id] = hip_id
                queue_hip("consent_notify", {"notification": {"status": "GRANTED", "consentId": artefact_id,
                                                              "consentDetail": detail, "signature": "simulated"}}, hip_id)
        notification = {"consentRequestId": consent_request_id, "status": "GRANTED", "reason": None,
                        "consentArtefacts": ids}
    queue("hiu_notify", {"notification": notification}, item["hiu_id"])
    from app.hip import simulator
    simulator.flush()
    return notification


def revoke(consent_request_id: str) -> None:
    item = consent_requests.get(consent_request_id)
    if item is None:
        raise ValueError("Unknown consent request (the simulator restarts empty)")
    item["status"] = "REVOKED"
    for artefact_id in item["artefacts"]:  # the HIPs stop sharing at once
        if artefact_id in local_hips:
            queue_hip("consent_notify", {"notification": {"status": "REVOKED", "consentId": artefact_id}},
                      local_hips[artefact_id])
    ids = [{"id": a} for a in item["artefacts"]]
    queue("hiu_notify", {"notification": {"consentRequestId": consent_request_id, "status": "REVOKED", "consentArtefacts": ids}},
          item["hiu_id"])
    from app.hip import simulator
    simulator.flush()


# ---------- the simulated external HIP ----------

def synthetic_records(patient: Patient) -> list[tuple[str, dict, datetime]]:
    """Five SYNTHETIC records at Sunrise Clinic (care context reference, bundle, record date)."""
    clinic = Hospital(id=900, name="Sunrise Clinic (SIMULATED)", code="SUN", city="Pune", state="Maharashtra")
    doctor = Doctor(id=901, full_name="Dr. Kavya Menon (simulated)", registration_number="SIM-55555",
                    qualification="MBBS, MD", specialization="Internal Medicine")
    external = Patient(id=902, patient_code="SUN-000777", full_name=patient.full_name, gender=patient.gender,
                       date_of_birth=patient.date_of_birth, year_of_birth=patient.year_of_birth, mobile=patient.mobile,
                       abha_verified=False)
    hba1c = Coding(system=LOINC, code="4548-4", display="Hemoglobin A1c/Hemoglobin.total in Blood")
    contents = [
        ("SUN-CC-1", ClinicalContent(record_type="OPConsultRecord", key="sim-op-1", title="OP consultation (synthetic)",
                                     date=datetime(2025, 6, 10, 10), encounter=EncounterData(start=datetime(2025, 6, 10, 9, 30)),
                                     chief_complaint="Follow-up of type 2 diabetes", plan="Continue metformin",
                                     conditions=[ConditionData(text="Type 2 diabetes mellitus", verification="confirmed")])),
        ("SUN-CC-2", ClinicalContent(record_type="PrescriptionRecord", key="sim-rx-1", title="Prescription (synthetic)",
                                     date=datetime(2025, 6, 10, 10, 15),
                                     medications=[MedicationData(name="Metformin 500 mg tablet", dosage="1 tablet", route="oral",
                                                                 frequency="BD", duration_days=90)])),
        ("SUN-CC-3", ClinicalContent(record_type="DiagnosticReportRecord", key="sim-lab-1", title="HbA1c (synthetic)",
                                     date=datetime(2025, 6, 10, 12), reports=[ReportData(
                                         name="HbA1c", results=[ResultData(name="HbA1c", loinc=hba1c, value="8.1", value_num=8.1,
                                                                           unit="%", ref_high=5.7, interpretation="H")],
                                         conclusion="Raised HbA1c", issued=datetime(2025, 6, 10, 12), interpreter=901)])),
        ("SUN-CC-4", ClinicalContent(record_type="DiagnosticReportRecord", key="sim-lab-2", title="HbA1c (synthetic)",
                                     date=datetime(2025, 12, 1, 12), reports=[ReportData(
                                         name="HbA1c", results=[ResultData(name="HbA1c", loinc=hba1c, value="7.2", value_num=7.2,
                                                                           unit="%", ref_high=5.7, interpretation="H")],
                                         conclusion="HbA1c improved but above target", issued=datetime(2025, 12, 1, 12),
                                         interpreter=901)])),
        ("SUN-CC-5", ClinicalContent(record_type="DischargeSummaryRecord", key="sim-ds-1", title="Discharge summary (synthetic)",
                                     date=datetime(2024, 11, 22, 11), encounter=EncounterData(
                                         start=datetime(2024, 11, 20), end=datetime(2024, 11, 22), inpatient=True),
                                     chief_complaint="Fever and cough", hospital_course="Treated with antibiotics; recovered",
                                     conditions=[ConditionData(text="Community acquired pneumonia", verification="confirmed",
                                                               clinical_status="resolved")])),
    ]
    return [(ref, build_bundle(c, external, clinic, doctor, [doctor]), c.date) for ref, c in contents]


def push(db, transaction_id: str, hi_request: dict) -> None:
    """Act as the external HIP: send only consented, in-range records, encrypted for the HIU's key."""
    from app.hiu import service
    from sqlalchemy import select
    artefact = artefacts.get(hi_request["consent"]["id"])
    patient = db.scalar(select(Patient).where(Patient.abha_address == artefact["patient"]["id"]))
    start = datetime.fromisoformat(hi_request["dateRange"]["from"].replace("Z", ""))
    end = datetime.fromisoformat(hi_request["dateRange"]["to"].replace("Z", ""))
    material, own = hi_request["keyMaterial"], crypto.generate_key_material()
    receiver_key, receiver_nonce = crypto.unb64(material["dhPublicKey"]["keyValue"]), crypto.unb64(material["nonce"])
    entries = []
    for reference, bundle, when in synthetic_records(patient):
        if HI_TYPE_OF[bundle["entry"][0]["resource"]["meta"]["profile"][0].rsplit("/", 1)[-1]] not in artefact["hiTypes"]:
            continue
        if not start <= when <= end:
            continue
        content = json.dumps(bundle)
        entries.append({"content": crypto.encrypt(content, own["private"], own["nonce"], receiver_key, receiver_nonce),
                        "media": "application/fhir+json", "checksum": hashlib.md5(content.encode()).hexdigest(),
                        "careContextReference": reference})
    body = {"pageNumber": 0, "pageCount": 1, "transactionId": transaction_id, "entries": entries,
            "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519",
                            "dhPublicKey": {"expiry": material["dhPublicKey"]["expiry"], "parameters": crypto.PARAMETERS,
                                            "keyValue": crypto.b64(own["x509"])},
                            "nonce": crypto.b64(own["nonce"])}}
    pushes.append({"transaction_id": transaction_id, "entries": [e["careContextReference"] for e in entries]})
    token = hi_request["dataPushUrl"].rsplit("/", 1)[-1]
    return service.receive_push(db, token, body)

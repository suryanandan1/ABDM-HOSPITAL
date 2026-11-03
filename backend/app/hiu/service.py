"""HIU workflow (ABDM Milestone 3): consent request -> notification -> artefact fetch and validation ->
health-information request -> asynchronous encrypted push -> decryption, storage and status notification.

Every step is a separate callback; nothing proceeds without a GRANTED artefact that validates against the
request. Reuses the HIP gateway client, transaction log and Fidelius-compatible crypto.
"""

import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import fhir_validation
from app.config import settings
from app.database import SessionLocal
from app.hip import crypto, gateway
from app.hip.service import parse_time, record
from app.hiu import records
from app.hiu.ai import HI_TYPES, PURPOSES
from app.models import (Doctor, HiuConsentArtefact, HiuConsentRequest, HiuDataRequest, Hospital, Patient,
                        RetrievedRecord, User, utcnow)
from app.services import audit

PURPOSE_REF_URI = "www.abdm.gov.in"  # as in NHA's consent request example
REQUESTER_SYSTEM = "https://www.mciindia.org"


class HiuError(Exception):
    pass


def iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds") + "Z"


def push_url(token: str) -> str:
    return f"{settings.abdm_bridge_url or 'sim://hiu'}/api/hiu/data-push/{token}"


def hiu_headers(request_id: str | None = None) -> dict:
    return {"X-HIU-ID": gateway.hiu_id(), **({"REQUEST-ID": request_id} if request_id else {})}


# ---------- 1. consent request ----------

def create_request(db: Session, user: User, patient: Patient, doctor: Doctor, purpose: str, hi_types: list[str],
                   date_from: datetime, date_to: datetime, erase_at: datetime, reason: str | None,
                   ai_suggestion: dict | None) -> HiuConsentRequest:
    if not (patient.abha_verified and patient.abha_address):
        raise HiuError("The patient's ABHA address must be verified with ABDM first (ABDM HIP page)")
    if purpose not in PURPOSES:
        raise HiuError("Unknown purpose of use")
    if not hi_types or not set(hi_types) <= set(HI_TYPES):
        raise HiuError("Choose at least one supported record category")
    if not date_from <= date_to <= utcnow() + timedelta(days=1):
        raise HiuError("The date range must end today or earlier and start before it ends")
    if erase_at <= utcnow():
        raise HiuError("The data erase date must be in the future")
    if not doctor.is_active or doctor.hospital_id != patient.hospital_id:
        raise HiuError("Choose an active doctor of this hospital as the requester")
    request_id = str(uuid.uuid4())
    request = HiuConsentRequest(
        hospital_id=patient.hospital_id, patient_id=patient.id, requested_by_id=user.id, requester_doctor_id=doctor.id,
        abha_address=patient.abha_address, purpose_code=purpose, hi_types=sorted(hi_types), date_from=date_from,
        date_to=date_to, data_erase_at=erase_at, reason=reason, ai_suggestion=ai_suggestion, request_id=request_id,
        status="requested", status_message="Sent to the consent manager; waiting for acknowledgement",
        mode=settings.abdm_mode)
    db.add(request)
    body = {"consent": {
        "purpose": {"text": PURPOSES[purpose], "code": purpose, "refUri": PURPOSE_REF_URI},
        "patient": {"id": patient.abha_address},
        "hiu": {"id": gateway.hiu_id()},
        "requester": {"name": doctor.full_name,
                      "identifier": {"type": "REGNO", "value": doctor.registration_number, "system": REQUESTER_SYSTEM}},
        "hiTypes": sorted(hi_types),
        "permission": {"accessMode": "VIEW", "dateRange": {"from": iso(date_from), "to": iso(date_to)},
                       "dataEraseAt": iso(erase_at), "frequency": {"unit": "DAY", "value": 0, "repeats": 0}}}}
    db.flush()
    tx = record(db, patient.hospital_id, "hiu_consent_init", "outbound", "pending", "Consent request sent",
                request_id=request_id, patient_id=patient.id,
                details={"purpose": purpose, "hiTypes": hi_types, "requestRecordId": request.id})
    audit(db, user, "hiu.consent_request", "patient", patient.id, {"purpose": purpose, "hi_types": hi_types,
                                                                   "requester": doctor.full_name})
    db.commit()
    status, data, _ = gateway.call(gateway.CONSENT_INIT_PATH, body, hiu_headers(request_id))
    tx.http_status = status
    if 200 <= status < 300:
        tx.message = "Accepted by the consent manager; waiting for on-init"
    else:
        error = (data or {}).get("error") or data or {}
        request.status, tx.status = "failed", "failed"
        request.status_message = tx.message = f"Consent manager refused the request (HTTP {status}): {error.get('message', '')}"
    db.commit()
    return request


def on_init(db: Session, hospital: Hospital, headers: dict, body: dict) -> str:
    request = db.scalar(select(HiuConsentRequest).where(
        HiuConsentRequest.request_id == (body.get("response") or {}).get("requestId")))
    if request is None:
        return "Unknown consent request acknowledgement"
    if body.get("error") or not (body.get("consentRequest") or {}).get("id"):
        request.status = "failed"
        request.status_message = f"Consent request rejected: {(body.get('error') or {}).get('message', 'no id returned')}"
    else:
        request.consent_request_id = body["consentRequest"]["id"]
        request.status, request.status_message = "pending_consent", "Waiting for the patient to grant or deny consent"
    return request.status_message


def check_status(db: Session, request: HiuConsentRequest) -> str:
    if not request.consent_request_id:
        raise HiuError("The consent manager has not acknowledged this request yet")
    status, data, request_id = gateway.call(gateway.CONSENT_STATUS_PATH, {"consentRequestId": request.consent_request_id},
                                            hiu_headers())
    record(db, request.hospital_id, "hiu_consent_status", "outbound", "success" if 200 <= status < 300 else "failed",
           "Consent status requested" if 200 <= status < 300 else f"Status request refused (HTTP {status})",
           request_id=request_id, patient_id=request.patient_id, http_status=status)
    db.commit()
    return "Status requested from the consent manager"


def on_status(db: Session, hospital: Hospital, headers: dict, body: dict) -> str:
    info = body.get("consentRequest") or {}
    request = db.scalar(select(HiuConsentRequest).where(HiuConsentRequest.consent_request_id == info.get("id")))
    if request is None:
        return "Status for an unknown consent request"
    status = (info.get("status") or "").upper()
    if status in ("DENIED", "EXPIRED", "REVOKED") and request.status in ("requested", "pending_consent", "granted"):
        request.status, request.status_message = status.lower(), f"Consent manager reports the request is {status}"
    return f"Consent request status: {status or 'unknown'}"


# ---------- 2. notification and artefact fetch ----------

def notify(db: Session, hospital: Hospital, headers: dict, body: dict) -> str:
    n = body.get("notification") or {}
    status, artefact_ids = (n.get("status") or "").upper(), [a.get("id") for a in n.get("consentArtefacts") or [] if a.get("id")]
    request = db.scalar(select(HiuConsentRequest).where(HiuConsentRequest.consent_request_id == n.get("consentRequestId")))
    ack = {"acknowledgement": [{"status": "OK", "consentId": a} for a in artefact_ids],
           "response": {"requestId": headers.get("request-id")}}
    message = "Notification for an unknown consent request"
    if request is not None:
        if status == "GRANTED":
            request.status, request.status_message = "granted", "Consent granted; fetching the consent artefact(s)"
            for artefact_id in artefact_ids:
                if not db.scalar(select(HiuConsentArtefact).where(HiuConsentArtefact.artefact_id == artefact_id)):
                    db.add(HiuConsentArtefact(consent_request=request, artefact_id=artefact_id))
        elif status in ("DENIED", "EXPIRED", "REVOKED"):
            request.status, request.status_message = status.lower(), f"Consent {status.lower()}" + (
                f": {n.get('reason')}" if n.get("reason") else "")
            for artefact in request.artefacts:
                if not artefact_ids or artefact.artefact_id in artefact_ids:
                    artefact.status, artefact.status_message = status.lower(), "Notified by the consent manager"
                    erase_records(db, artefact.artefact_id)  # data may not be used once consent ends
        message = request.status_message
    db.commit()
    gateway.call(gateway.CONSENT_HIU_ON_NOTIFY_PATH, ack, hiu_headers())
    if request is not None and status == "GRANTED":
        for artefact in request.artefacts:
            if artefact.status == "notified":
                fetch_artefact(db, artefact)
    return message


def fetch_artefact(db: Session, artefact: HiuConsentArtefact) -> None:
    request_id = str(uuid.uuid4())
    artefact.fetch_request_id, artefact.status_message = request_id, "Fetching the artefact"
    db.commit()
    status, _, _ = gateway.call(gateway.CONSENT_FETCH_PATH, {"consentId": artefact.artefact_id}, hiu_headers(request_id))
    record(db, artefact.consent_request.hospital_id, "hiu_consent_fetch", "outbound",
           "pending" if 200 <= status < 300 else "failed",
           "Artefact requested" if 200 <= status < 300 else f"Artefact fetch refused (HTTP {status}); retry from the request",
           request_id=request_id, consent_id=artefact.artefact_id, patient_id=artefact.consent_request.patient_id,
           http_status=status)
    db.commit()


def artefact_problem(request: HiuConsentRequest, artefact_id: str, status: str, detail: dict) -> str | None:
    """The artefact must be granted and exactly within what this hospital asked for."""
    permission = detail.get("permission") or {}
    if status != "GRANTED":
        return f"Artefact status is {status or 'missing'}"
    if detail.get("consentId") != artefact_id:
        return "Artefact id does not match"
    if (detail.get("hiu") or {}).get("id") != gateway.hiu_id():
        return "Artefact is for a different HIU"
    if ((detail.get("patient") or {}).get("id") or "").lower() != request.abha_address.lower():
        return "Artefact is for a different patient"
    if not (detail.get("hip") or {}).get("id"):
        return "Artefact names no health information provider"
    if not detail.get("hiTypes") or not set(detail["hiTypes"]) <= set(request.hi_types):
        return "Artefact covers record types that were not requested"
    if (detail.get("purpose") or {}).get("code") != request.purpose_code:
        return "Artefact purpose differs from the request"
    if permission.get("accessMode") != "VIEW":
        return "Artefact access mode is not VIEW"
    try:
        start, end = (parse_time((permission.get("dateRange") or {}).get(k)) for k in ("from", "to"))
        erase_at = parse_time(permission.get("dataEraseAt"))
    except ValueError:
        return "Artefact dates are invalid"
    if not (start and end and erase_at) or start > end:
        return "Artefact has no valid date range or erase date"
    if start < request.date_from - timedelta(seconds=1) or end > request.date_to + timedelta(seconds=1):
        return "Artefact date range is wider than requested"
    if erase_at <= utcnow():
        return "Artefact has already expired"
    if not detail.get("careContexts"):
        return "Artefact lists no care contexts"
    return None


def on_fetch(db: Session, hospital: Hospital, headers: dict, body: dict) -> str:
    artefact = db.scalar(select(HiuConsentArtefact).where(
        HiuConsentArtefact.fetch_request_id == (body.get("response") or {}).get("requestId")))
    if artefact is None:
        return "Artefact for an unknown fetch"
    consent, request = body.get("consent") or {}, artefact.consent_request
    if body.get("error"):
        artefact.status, artefact.status_message = "invalid", f"Fetch failed: {body['error'].get('message')}"
        return artefact.status_message
    detail = consent.get("consentDetail") or {}
    problem = artefact_problem(request, artefact.artefact_id, (consent.get("status") or "").upper(), detail)
    artefact.detail, artefact.fetched_at = detail, utcnow()
    if problem:
        artefact.status, artefact.status_message = "invalid", f"Artefact not accepted: {problem}"
        if all(a.status == "invalid" for a in request.artefacts):
            request.status, request.status_message = "failed", artefact.status_message
        return artefact.status_message
    permission = detail["permission"]
    artefact.status, artefact.hip_id = "valid", detail["hip"]["id"]
    artefact.hi_types, artefact.care_contexts = detail["hiTypes"], detail["careContexts"]
    artefact.date_from = parse_time(permission["dateRange"]["from"])
    artefact.date_to = parse_time(permission["dateRange"]["to"])
    artefact.data_erase_at = parse_time(permission["dataEraseAt"])
    artefact.status_message = f"Valid consent from {artefact.hip_id} for {', '.join(artefact.hi_types)}"
    db.commit()
    request_data(db, artefact)  # permitted automation: a valid artefact is exactly what the patient approved
    return artefact.status_message


# ---------- 3. health information request and data reception ----------

def request_data(db: Session, artefact: HiuConsentArtefact) -> HiuDataRequest:
    if artefact.status != "valid" or not artefact.data_erase_at or artefact.data_erase_at <= utcnow():
        raise HiuError("A valid, unexpired consent artefact is required")
    busy = db.scalar(select(HiuDataRequest).where(HiuDataRequest.artefact_id == artefact.id,
                                                  HiuDataRequest.status.in_(("requested", "acknowledged"))))
    if busy:
        raise HiuError("A data request for this consent is already in progress")
    keys = crypto.generate_key_material()
    request_id, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
    data_request = HiuDataRequest(
        artefact_id=artefact.id, request_id=request_id, push_token=token, status="requested",
        key_material=records.seal(json.dumps({"private": str(keys["private"]), "nonce": crypto.b64(keys["nonce"])})),
        expires_at=utcnow() + timedelta(hours=24), status_message="Waiting for the provider to send data")
    db.add(data_request)
    body = {"hiRequest": {
        "consent": {"id": artefact.artefact_id},
        "dateRange": {"from": iso(artefact.date_from), "to": iso(artefact.date_to)},
        "dataPushUrl": push_url(token),
        "keyMaterial": {"cryptoAlg": crypto.CRYPTO_ALG, "curve": crypto.CURVE,
                        "dhPublicKey": {"expiry": iso(data_request.expires_at), "parameters": crypto.PARAMETERS,
                                        "keyValue": crypto.b64(keys["public"])},
                        "nonce": crypto.b64(keys["nonce"])}}}
    request = artefact.consent_request
    request.status, request.status_message = "retrieving", "Health information requested; waiting for data"
    tx = record(db, request.hospital_id, "hiu_data_request", "outbound", "pending", "Health information requested",
                request_id=request_id, consent_id=artefact.artefact_id, patient_id=request.patient_id)
    db.commit()
    status, data, _ = gateway.call(gateway.HI_REQUEST_PATH, body, hiu_headers(request_id))
    tx.http_status = status
    if not 200 <= status < 300:
        data_request.status = tx.status = "failed"
        data_request.status_message = tx.message = f"Data request refused (HTTP {status}); retry from the request"
        data_request.key_material = None
        request.status_message = data_request.status_message
    db.commit()
    return data_request


def on_request(db: Session, hospital: Hospital, headers: dict, body: dict) -> str:
    data_request = db.scalar(select(HiuDataRequest).where(
        HiuDataRequest.request_id == (body.get("response") or {}).get("requestId")))
    if data_request is None:
        return "Acknowledgement for an unknown data request"
    if body.get("error") or not (body.get("hiRequest") or {}).get("transactionId"):
        data_request.status, data_request.key_material = "failed", None
        data_request.status_message = f"Data request rejected: {(body.get('error') or {}).get('message', 'no transaction')}"
        data_request.artefact.consent_request.status_message = data_request.status_message
    else:
        data_request.transaction_id = body["hiRequest"]["transactionId"]
        if data_request.status == "requested":
            data_request.status = "acknowledged"
        data_request.status_message = f"Acknowledged ({body['hiRequest'].get('sessionStatus')}); waiting for data"
    return data_request.status_message


def receive_push(db: Session, token: str, body: dict) -> tuple[int, str, list[int]]:
    """Data pushed by the HIP to the one-time dataPushUrl: decrypt, store sealed, then notify the CM."""
    data_request = db.scalar(select(HiuDataRequest).where(HiuDataRequest.push_token == token))
    if data_request is None:
        return 404, "Unknown push URL", []
    artefact, request = data_request.artefact, data_request.artefact.consent_request
    if data_request.key_material is None or data_request.status not in ("requested", "acknowledged"):
        return 409, "This push URL is closed", []
    if data_request.expires_at < utcnow() or artefact.status != "valid":
        data_request.status, data_request.key_material = "expired", None
        db.commit()
        return 410, "The data request or consent has expired", []
    if data_request.transaction_id and body.get("transactionId") != data_request.transaction_id:
        return 400, "Transaction id does not match", []
    keys = json.loads(records.unseal(data_request.key_material))
    material = body.get("keyMaterial") or {}
    allowed = {c["careContextReference"] for c in artefact.care_contexts or []}
    statuses, stored = [], []
    for entry in body.get("entries") or []:
        reference = entry.get("careContextReference")
        try:
            if reference not in allowed:
                raise ValueError("care context not covered by the consent")
            content = crypto.decrypt(entry["content"], int(keys["private"]), crypto.unb64(keys["nonce"]),
                                     crypto.unb64(material["dhPublicKey"]["keyValue"]), crypto.unb64(material["nonce"]))
            bundle = json.loads(content)
            if bundle.get("resourceType") != "Bundle":
                raise ValueError("content is not a FHIR Bundle")
        except Exception as exc:  # any undecryptable or invalid entry is reported, never stored
            statuses.append({"careContextReference": reference, "hiStatus": "ERRORED",
                             "description": f"Could not accept: {type(exc).__name__}"})
            continue
        facts = records.describe(bundle)
        stored.append(RetrievedRecord(
            hospital_id=request.hospital_id, patient_id=request.patient_id, data_request_id=data_request.id,
            consent_artefact_id=artefact.artefact_id, source_hip_id=artefact.hip_id, source_name=facts["source"],
            care_context_reference=reference, record_type=facts["record_type"], category=facts["category"],
            record_date=parse_time(facts["date"]) if facts["date"] else None, content=records.seal(content),
            checksum_ok=hashlib.md5(content.encode()).hexdigest() == entry.get("checksum"),
            validation_issues=fhir_validation.prechecks(bundle), erase_at=artefact.data_erase_at, mode=settings.abdm_mode))
        db.add(stored[-1])
        statuses.append({"careContextReference": reference, "hiStatus": "OK", "description": "Received"})
    data_request.pages_expected = int(body.get("pageCount") or 1)
    data_request.pages_received += 1
    data_request.transaction_id = data_request.transaction_id or body.get("transactionId")
    received = sum(s["hiStatus"] == "OK" for s in statuses)
    done = data_request.pages_received >= data_request.pages_expected
    if done:
        data_request.status, data_request.key_material = "received", None  # key destroyed: no reuse of the push URL
        data_request.status_message = f"{received} record(s) received and stored"
        request.status, request.status_message = "received", data_request.status_message
    record(db, request.hospital_id, "hiu_data_push", "inbound", "success" if received else "failed",
           f"Page {data_request.pages_received}/{data_request.pages_expected}: {received} of {len(statuses)} record(s) accepted",
           transaction_id=data_request.transaction_id, consent_id=artefact.artefact_id, patient_id=request.patient_id,
           details={"statusResponses": statuses})
    audit(db, None, "hiu.data_received", "patient", request.patient_id,
          {"consent": artefact.artefact_id, "records": received}, hospital_id=request.hospital_id)
    db.commit()
    notification = {"notification": {
        "consentId": artefact.artefact_id, "transactionId": data_request.transaction_id, "doneAt": gateway.timestamp(),
        "notifier": {"type": "HIU", "id": gateway.hiu_id()},
        "statusNotification": {"sessionStatus": "TRANSFERRED" if received else "FAILED", "hipId": artefact.hip_id,
                               "statusResponses": statuses}}}
    gateway.call(gateway.HI_NOTIFY_PATH, notification, hiu_headers())
    ids = [r.id for r in stored]
    if ids and settings.hiu_auto_validate and fhir_validation.validator_available():
        threading.Thread(target=lambda: [validate_record(i) for i in ids], daemon=True).start()
    return 202, data_request.status_message or "Accepted", ids


# ---------- retention ----------

def erase_records(db: Session, artefact_id: str) -> int:
    rows = db.scalars(select(RetrievedRecord).where(RetrievedRecord.consent_artefact_id == artefact_id)).all()
    for row in rows:
        db.delete(row)
    return len(rows)


def enforce_retention(db: Session) -> None:
    """Erase records past the consent's dataEraseAt; close data requests that never received data."""
    now = utcnow()
    for row in db.scalars(select(RetrievedRecord).where(RetrievedRecord.erase_at <= now)):
        db.delete(row)
    for artefact in db.scalars(select(HiuConsentArtefact).where(HiuConsentArtefact.status == "valid",
                                                                HiuConsentArtefact.data_erase_at <= now)):
        artefact.status, artefact.status_message = "expired", "Consent expired (dataEraseAt passed); data erased"
    for data_request in db.scalars(select(HiuDataRequest).where(HiuDataRequest.status.in_(("requested", "acknowledged")),
                                                                HiuDataRequest.expires_at <= now)):
        data_request.status, data_request.key_material = "expired", None
        data_request.status_message = "No data arrived before the request expired; retry if consent is still valid"
    db.commit()


def validate_record(record_id: int) -> None:
    """HL7 FHIR validation of a received bundle (background)."""
    with SessionLocal() as db:
        row = db.get(RetrievedRecord, record_id)
        if row is None:
            return
        status, issues, _ = fhir_validation.validate(records.bundle_json(row))
        row.validation_status, row.validation_issues = status, issues
        db.commit()


# ---------- inbound dispatch ----------

INBOUND = {"hiu_on_init": on_init, "hiu_on_status": on_status, "hiu_notify": notify, "hiu_on_fetch": on_fetch,
           "hiu_on_request": on_request}


def handle_inbound(kind: str, headers: dict, body: dict):
    headers = {k.lower(): v for k, v in headers.items()}
    with SessionLocal() as db:
        from app.hip.service import hip_hospital
        hospital = hip_hospital(db)
        try:
            message = INBOUND[kind](db, hospital, headers, body)
            status = "failed" if body.get("error") else "success"
        except (gateway.GatewayError, HiuError) as exc:
            db.rollback()
            message, status = f"Could not complete: {exc}", "failed"
        tx = record(db, hospital.id, kind, "inbound", status, message, request_id=(body.get("response") or {}).get("requestId"),
                    consent_id=((body.get("notification") or {}).get("consentRequestId")),
                    details={"error": body.get("error")})  # artefacts carry patient identifiers: not logged
        db.commit()
        return tx

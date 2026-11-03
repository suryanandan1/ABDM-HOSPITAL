"""HIP workflows: identity linking, care contexts, discovery, user-initiated linking, consent and data transfer.

Message shapes follow NHA's ABDM V3 contracts (sandbox documentation + NHA ABDM wrapper). Inbound callbacks
are processed by handle_inbound(); every exchange is recorded as a HipTransaction with redacted details.
Health records are never decrypted, logged or returned here: only consented, HIP-eligible FHIR records are
encrypted and pushed to the HIU.
"""

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.hip import crypto, gateway
from app.models import (CareContext, FhirRecord, HipConsent, HipLinkRequest, HipPatientLink, HipTransaction, Hospital,
                        Patient, User, utcnow)
from app.services import audit, name_similarity

HI_TYPES = {"OPConsultRecord": "OPConsultation", "PrescriptionRecord": "Prescription",
            "DiagnosticReportRecord": "DiagnosticReport", "DischargeSummaryRecord": "DischargeSummary",
            "ImmunizationRecord": "ImmunizationRecord", "WellnessRecord": "WellnessRecord",
            "HealthDocumentRecord": "HealthDocumentRecord"}
HI_LABELS = {"OPConsultation": "OP consultation", "Prescription": "Prescription", "DiagnosticReport": "Lab report",
             "DischargeSummary": "Discharge summary", "ImmunizationRecord": "Immunization",
             "WellnessRecord": "Wellness record", "HealthDocumentRecord": "Health document"}
SECRET_KEYS = {"linktoken", "token", "keyvalue", "content", "nonce", "otp", "accesstoken", "signature", "x-link-token",
               "authorization", "clientsecret"}
PATIENT_NOT_FOUND = ("ABDM-1010", "Patient not found")  # codes as used by NHA's ABDM wrapper
INCORRECT_OTP = ("ABDM-1035", "Incorrect OTP")
GENERIC_ERROR = "1000"
PUSH_PAGE_SIZE = 7  # entries per data-push page, as in NHA's wrapper


class HipError(Exception):
    """A request the HIP refuses; message is shown to staff."""


# ---------- helpers ----------

def redact(node):
    if isinstance(node, dict):
        return {k: "***" if k.lower() in SECRET_KEYS else redact(v) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v) for v in node]
    return node


def record(db: Session, hospital_id: int, kind: str, direction: str, status: str, message: str, **fields) -> HipTransaction:
    tx = HipTransaction(hospital_id=hospital_id, mode=settings.abdm_mode, kind=kind, direction=direction,
                        status=status, message=message[:500], **{k: v for k, v in fields.items() if k != "details"},
                        details=redact(fields.get("details")))
    db.add(tx)
    return tx


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def hip_hospital(db: Session) -> Hospital:
    """The hospital this HIP ID belongs to: matched by HFR ID, or the only hospital."""
    hospital = db.scalar(select(Hospital).where(Hospital.hfr_id == gateway.hip_id()))
    if hospital is None:
        hospitals = db.scalars(select(Hospital)).all()
        if len(hospitals) != 1:
            raise HipError("Set the hospital's HFR ID to ABDM_HIP_ID so callbacks can be routed")
        hospital = hospitals[0]
    return hospital


def otp_hash(otp: str, link_reference: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"{link_reference}:{otp}".encode(), hashlib.sha256).hexdigest()


def masked(mobile: str) -> str:
    return "X" * (len(mobile) - 4) + mobile[-4:]


def check_accepted(db: Session, tx: HipTransaction, status: int, data: dict | None, success: str) -> bool:
    tx.http_status = status
    if 200 <= status < 300:
        tx.message = success
        return True
    error = (data or {}).get("error") or {}
    tx.status, tx.message = "failed", f"ABDM refused the request (HTTP {status}): {error.get('message', 'no details')}"
    return False


# ---------- care contexts ----------

def record_date(record: FhirRecord) -> datetime:
    composition = record.bundle["entry"][0]["resource"]
    return parse_time(composition.get("date")) or record.generated_at


def sync_care_contexts(db: Session, patient: Patient) -> list[CareContext]:
    """One care context per HIP-eligible (validated) FHIR record of the patient."""
    existing = {c.fhir_record_id for c in db.scalars(select(CareContext).where(CareContext.patient_id == patient.id))}
    hospital = db.get(Hospital, patient.hospital_id)
    for rec in db.scalars(select(FhirRecord).where(FhirRecord.patient_id == patient.id)):
        if rec.id in existing or not rec.hip_eligible:
            continue
        hi_type, when = HI_TYPES[rec.record_type], record_date(rec)
        db.add(CareContext(hospital_id=patient.hospital_id, patient_id=patient.id, fhir_record_id=rec.id,
                           reference=f"{hospital.code}-{rec.source_type.upper().replace('_', '-')}-{rec.source_id}",
                           display=f"{HI_LABELS[hi_type]} · {when:%d %b %Y}", hi_type=hi_type, record_date=when))
    db.flush()
    return db.scalars(select(CareContext).where(CareContext.patient_id == patient.id).order_by(CareContext.id)).all()


def grouped(patient: Patient, contexts: list[CareContext]) -> list[dict]:
    """Care contexts grouped by HI type, the shape used by on-discover, on-confirm and add care contexts."""
    groups: dict[str, list[CareContext]] = {}
    for c in contexts:
        groups.setdefault(c.hi_type, []).append(c)
    return [{"referenceNumber": patient.patient_code, "display": patient.full_name, "hiType": hi_type,
             "count": len(items), "careContexts": [{"referenceNumber": c.reference, "display": c.display} for c in items]}
            for hi_type, items in groups.items()]


# ---------- HIP-initiated linking (staff) ----------

def request_link_token(db: Session, user: User, patient: Patient) -> HipTransaction:
    """Ask ABDM to confirm the patient's ABHA address against their demographics and issue a link token."""
    if not patient.abha_address:
        raise HipError("Enter the patient's ABHA address first")
    if patient.gender not in ("M", "F", "O"):
        raise HipError("ABDM linking needs gender M, F or O")
    body = {"abhaAddress": patient.abha_address, "name": patient.full_name, "gender": patient.gender,
            "yearOfBirth": patient.year_of_birth}
    request_id = str(uuid.uuid4())
    link = db.scalar(select(HipPatientLink).where(HipPatientLink.patient_id == patient.id)) or \
        HipPatientLink(hospital_id=patient.hospital_id, patient_id=patient.id)
    link.abha_address, link.status, link.request_id, link.link_token = patient.abha_address, "pending", request_id, None
    link.message, link.mode = "Waiting for ABDM to verify the ABHA address", settings.abdm_mode
    db.add(link)
    tx = record(db, patient.hospital_id, "generate_link_token", "outbound", "pending", "Sending to ABDM",
                request_id=request_id, patient_id=patient.id, details=body)
    audit(db, user, "hip.link_token_request", "patient", patient.id)
    db.commit()
    status, data, _ = gateway.call(gateway.GENERATE_LINK_TOKEN_PATH, body,
                                   {"REQUEST-ID": request_id, "X-HIP-ID": gateway.hip_id()})
    if not check_accepted(db, tx, status, data, "Accepted by ABDM; waiting for the link token callback"):
        link.status, link.message = "failed", tx.message
    db.commit()
    return tx


def on_generate_token(db: Session, body: dict) -> str:
    request_id = (body.get("response") or {}).get("requestId")
    link = db.scalar(select(HipPatientLink).where(HipPatientLink.request_id == request_id))
    if link is None:
        return "Unknown link token response"
    patient, error = link.patient, body.get("error")
    if error or not body.get("linkToken"):
        link.status = "failed"
        link.message = f"ABDM could not verify {link.abha_address}: {(error or {}).get('message', 'no link token')}"
        return link.message
    if (body.get("abhaAddress") or "").lower() != link.abha_address.lower():
        link.status, link.message = "failed", "ABDM answered for a different ABHA address; nothing was linked"
        return link.message
    link.status, link.link_token = "verified", body["linkToken"]
    link.message = "ABHA address verified by ABDM (demographic match); link token received"
    claims = jwt.decode(body["linkToken"], options={"verify_signature": False})  # token itself is opaque to the HIP
    abha_number = re.sub(r"\D", "", claims.get("abhaNumber") or "")
    if patient.abha_number and abha_number and patient.abha_number != abha_number:
        link.message += "; the ABHA number on file differs from ABDM's and was not marked verified"
    else:
        patient.abha_number = patient.abha_number or abha_number or None
        patient.abha_verified, patient.abha_verified_at = True, utcnow()
        patient.abha_verification_method = "abdm_link_token"
    return link.message


def link_care_contexts(db: Session, user: User, patient: Patient, contexts: list[CareContext]) -> HipTransaction:
    """Add care contexts to the patient's ABHA address with the link token (HIP-initiated linking)."""
    link = db.scalar(select(HipPatientLink).where(HipPatientLink.patient_id == patient.id))
    if link is None or link.status != "verified" or not link.link_token:
        raise HipError("Verify the patient's ABHA address with ABDM (link token) first")
    if not contexts:
        raise HipError("No care contexts to link")
    claims = jwt.decode(link.link_token, options={"verify_signature": False})
    body = {"abhaNumber": claims.get("abhaNumber"), "abhaAddress": link.abha_address, "patient": grouped(patient, contexts)}
    request_id = str(uuid.uuid4())
    for c in contexts:
        c.link_status, c.request_id, c.message = "linking", request_id, "Waiting for ABDM"
    tx = record(db, patient.hospital_id, "add_care_contexts", "outbound", "pending", "Sending to ABDM",
                request_id=request_id, patient_id=patient.id, details={"careContexts": [c.reference for c in contexts]})
    audit(db, user, "hip.care_context_link", "patient", patient.id, {"care_contexts": [c.reference for c in contexts]})
    db.commit()
    status, data, _ = gateway.call(gateway.ADD_CARE_CONTEXTS_PATH, body,
                                   {"REQUEST-ID": request_id, "X-HIP-ID": gateway.hip_id(), "X-LINK-TOKEN": link.link_token})
    if not check_accepted(db, tx, status, data, "Accepted by ABDM; waiting for confirmation"):
        for c in contexts:
            c.link_status, c.message = "failed", tx.message
    db.commit()
    return tx


def on_add_care_contexts(db: Session, body: dict) -> str:
    request_id = (body.get("response") or {}).get("requestId")
    contexts = db.scalars(select(CareContext).where(CareContext.request_id == request_id)).all()
    if not contexts:
        return "Unknown add-care-context response"
    error = body.get("error")
    for c in contexts:
        if error:
            c.link_status, c.message = "failed", f"ABDM did not link: {error.get('message', 'unknown error')}"
        else:
            c.link_status, c.linked_abha_address, c.linked_at = "linked", body.get("abhaAddress"), utcnow()
            c.link_method, c.message = "hip_initiated", body.get("status") or "Linked"
    return contexts[0].message


# ---------- discovery and user-initiated linking (inbound) ----------

def match_patient(db: Session, hospital_id: int, p: dict) -> tuple[Patient | None, str | None]:
    """Find exactly one patient: by ABHA address, else by verified mobile or MR number, always with demographics."""
    gender, name, yob = (p.get("gender") or "").upper()[:1], p.get("name") or "", int(p.get("yearOfBirth") or 0)

    def demographics_match(candidate: Patient) -> bool:
        return (candidate.gender == gender and abs(candidate.year_of_birth - yob) <= 1
                and name_similarity(candidate.full_name, name) >= 0.85)

    patients = select(Patient).where(Patient.hospital_id == hospital_id)
    address = (p.get("id") or "").lower()
    by_abha = [c for c in db.scalars(patients.where(Patient.abha_address == address)) if demographics_match(c)]
    if len(by_abha) == 1:
        return by_abha[0], "ABHA_ADDRESS"
    for kind in ("MOBILE", "MR"):
        values = {i.get("value") for i in (p.get("verifiedIdentifiers") or []) + (p.get("unverifiedIdentifiers") or [])
                  if i.get("type") == kind and i.get("value")}
        for value in values:
            column = Patient.mobile if kind == "MOBILE" else Patient.patient_code
            key = re.sub(r"\D", "", value)[-10:] if kind == "MOBILE" else value
            found = [c for c in db.scalars(patients.where(column == key)) if demographics_match(c)]
            if kind == "MOBILE" and not any(i.get("type") == "MOBILE" for i in p.get("verifiedIdentifiers") or []):
                found = []  # an unverified mobile is never enough
            if len(found) == 1:  # ambiguous matches are never returned
                return found[0], kind
    return None, None


def discover(db: Session, hospital: Hospital, headers: dict, body: dict) -> HipTransaction:
    patient_info, transaction_id = body.get("patient") or {}, body.get("transactionId")
    patient, matched_by = match_patient(db, hospital.id, patient_info)
    response = {"transactionId": transaction_id, "response": {"requestId": headers.get("request-id")}}
    contexts = [c for c in sync_care_contexts(db, patient) if c.link_status != "linked"] if patient else []
    if patient is None:
        response["error"] = dict(zip(("code", "message"), PATIENT_NOT_FOUND))
        message = "No single matching patient (nothing revealed)"
    elif not contexts:
        response["error"] = {"code": GENERIC_ERROR, "message": "Care Contexts not found for patient"}
        message = "Patient matched but has no unlinked shareable records"
    else:
        response.update(patient=grouped(patient, contexts), matchedBy=[matched_by])
        message = f"Patient matched by {matched_by}; {len(contexts)} care context(s) offered"
    tx = record(db, hospital.id, "discover", "inbound", "pending", message, transaction_id=transaction_id,
                request_id=headers.get("request-id"), patient_id=patient.id if patient else None,
                details={"matchedBy": matched_by, "careContexts": [c.reference for c in contexts]})
    db.commit()
    status, data, _ = gateway.call(gateway.ON_DISCOVER_PATH, response, {"X-HIP-ID": gateway.hip_id()})
    if check_accepted(db, tx, status, data, message):
        tx.status = "success"  # a "no match" answer is still a completed discovery
    return tx


def link_init(db: Session, hospital: Hospital, headers: dict, body: dict) -> HipTransaction:
    transaction_id, address = body.get("transactionId"), (body.get("abhaAddress") or "").lower()
    groups = body.get("patient") or []
    patient_code = groups[0].get("referenceNumber") if groups else None
    refs = [c.get("referenceNumber") for g in groups for c in g.get("careContexts") or []]
    patient = db.scalar(select(Patient).where(Patient.hospital_id == hospital.id, Patient.patient_code == patient_code))
    contexts = db.scalars(select(CareContext).where(CareContext.reference.in_(refs))).all() if patient else []
    response = {"transactionId": transaction_id, "response": {"requestId": headers.get("request-id")}}
    problem = None
    if patient is None:
        problem = PATIENT_NOT_FOUND
    elif not refs or len(contexts) != len(set(refs)) or any(c.patient_id != patient.id or c.link_status == "linked"
                                                             for c in contexts):
        problem = (GENERIC_ERROR, "Care contexts do not belong to this patient or are already linked")
    elif patient.abha_verified and patient.abha_address and patient.abha_address != address:
        problem = (GENERIC_ERROR, "This patient is already linked to a different ABHA address")
    elif not (gateway.simulation() or settings.abdm_otp_test_display):
        problem = (GENERIC_ERROR, "The HIP cannot deliver the OTP: no SMS gateway is configured")
    if problem:
        response["error"] = dict(zip(("code", "message"), problem))
        tx = record(db, hospital.id, "link_init", "inbound", "failed", f"Link request refused: {problem[1]}",
                    transaction_id=transaction_id, patient_id=patient.id if patient else None)
    else:
        otp, reference = f"{secrets.randbelow(10**6):06d}", str(uuid.uuid4())
        expires = utcnow() + timedelta(minutes=10)
        db.add(HipLinkRequest(hospital_id=hospital.id, patient_id=patient.id, transaction_id=transaction_id,
                              link_reference=reference, abha_address=address, care_context_refs=refs,
                              otp_hash=otp_hash(otp, reference), otp_expires_at=expires, mode=settings.abdm_mode,
                              test_otp=otp))  # delivered to the patient below; cleared once used
        if gateway.simulation():
            from app.hip import simulator
            simulator.deliver_sms(patient.mobile, otp)
        response["link"] = {"referenceNumber": reference, "authenticationType": "MEDIATE",
                            "meta": {"communicationMedium": "MOBILE", "communicationHint": masked(patient.mobile),
                                     "communicationExpiry": expires.isoformat(timespec="milliseconds") + "Z"}}
        tx = record(db, hospital.id, "link_init", "inbound", "pending",
                    f"OTP sent to the patient's registered mobile {masked(patient.mobile)}",
                    transaction_id=transaction_id, patient_id=patient.id, details={"careContexts": refs})
    db.commit()
    status, data, _ = gateway.call(gateway.ON_INIT_LINK_PATH, response, {"X-HIP-ID": gateway.hip_id()})
    if check_accepted(db, tx, status, data, tx.message) and not problem:
        tx.status = "success"
    return tx


def link_confirm(db: Session, hospital: Hospital, headers: dict, body: dict) -> HipTransaction:
    confirmation = body.get("confirmation") or {}
    request = db.scalar(select(HipLinkRequest).where(HipLinkRequest.link_reference == confirmation.get("linkRefNumber")))
    response = {"response": {"requestId": headers.get("request-id")}}
    problem = None
    if request is None or request.status != "otp_sent":
        problem = (GENERIC_ERROR, "Unknown or completed link request")
    elif request.otp_expires_at < utcnow() or request.attempts >= 3:
        request.status, problem = "failed", (GENERIC_ERROR, "OTP expired or too many attempts")
    elif not hmac.compare_digest(request.otp_hash, otp_hash(str(confirmation.get("token") or ""), request.link_reference)):
        request.attempts += 1
        problem = INCORRECT_OTP
    if problem:
        response["error"] = dict(zip(("code", "message"), problem))
        tx = record(db, hospital.id, "link_confirm", "inbound", "failed", f"Link not confirmed: {problem[1]}",
                    patient_id=request.patient_id if request else None)
    else:
        patient = db.get(Patient, request.patient_id)
        contexts = db.scalars(select(CareContext).where(CareContext.reference.in_(request.care_context_refs))).all()
        for c in contexts:
            c.link_status, c.linked_abha_address, c.linked_at = "linked", request.abha_address, utcnow()
            c.link_method, c.message = "patient_initiated", "Linked by the patient from their ABHA app"
        request.status, request.test_otp = "linked", None
        if not patient.abha_address:
            patient.abha_address = request.abha_address
        if patient.abha_address == request.abha_address:  # the patient authenticated with ABDM and proved the mobile
            patient.abha_verified, patient.abha_verified_at = True, utcnow()
            patient.abha_verification_method = "abdm_patient_linking"
        response["patient"] = grouped(patient, contexts)
        tx = record(db, hospital.id, "link_confirm", "inbound", "pending",
                    f"{len(contexts)} care context(s) linked to {request.abha_address}", patient_id=patient.id,
                    details={"careContexts": request.care_context_refs})
    db.commit()
    status, data, _ = gateway.call(gateway.ON_CONFIRM_LINK_PATH, response, {"X-HIP-ID": gateway.hip_id()})
    if check_accepted(db, tx, status, data, tx.message) and not problem:
        tx.status = "success"
    return tx


# ---------- consent ----------

def consent_problem(db: Session, hospital: Hospital, detail: dict) -> tuple[str | None, Patient | None]:
    """Checks a GRANTED consent artefact against this HIP's records. Returns (problem, patient)."""
    if (detail.get("hip") or {}).get("id") != gateway.hip_id():
        return "Consent is for a different HIP", None
    address = ((detail.get("patient") or {}).get("id") or "").lower()
    refs = detail.get("careContexts") or []
    contexts = db.scalars(select(CareContext).where(
        CareContext.reference.in_([r.get("careContextReference") for r in refs]))).all()
    if not refs or len(contexts) != len(refs):
        return "Consent lists care contexts this HIP does not hold", None
    if any(c.link_status != "linked" or (c.linked_abha_address or "").lower() != address for c in contexts):
        return "Consent lists care contexts not linked to this ABHA address", None
    patient = db.get(Patient, contexts[0].patient_id)
    if any(c.patient_id != patient.id for c in contexts) or any(r.get("patientReference") != patient.patient_code for r in refs):
        return "Consent mixes records of different patients", None
    permission = detail.get("permission") or {}
    try:
        erase_at = parse_time(permission.get("dataEraseAt"))
        start, end = (parse_time((permission.get("dateRange") or {}).get(k)) for k in ("from", "to"))
    except ValueError:
        return "Consent permission dates are invalid", patient
    if not (erase_at and start and end) or start > end:
        return "Consent permission is missing its date range or erase date", patient
    if erase_at < utcnow():
        return "Consent has already expired (dataEraseAt passed)", patient
    if not set(detail.get("hiTypes") or []) <= set(HI_TYPES.values()) or not detail.get("hiTypes"):
        return "Consent contains unsupported health information types", patient
    return None, patient


def consent_notify(db: Session, hospital: Hospital, headers: dict, body: dict) -> HipTransaction:
    notification = body.get("notification") or {}
    status, consent_id = (notification.get("status") or "").upper(), notification.get("consentId")
    detail = notification.get("consentDetail") or {}
    consent = db.scalar(select(HipConsent).where(HipConsent.consent_id == consent_id))
    response = {"response": {"requestId": headers.get("request-id")}}
    if status == "GRANTED":
        problem, patient = consent_problem(db, hospital, detail)
        permission = detail.get("permission") or {}
        consent = consent or HipConsent(hospital_id=hospital.id, consent_id=consent_id, mode=settings.abdm_mode)
        consent.patient_id, consent.abha_address = patient.id if patient else None, (detail.get("patient") or {}).get("id")
        consent.hiu_id, consent.purpose = (detail.get("hiu") or {}).get("id"), (detail.get("purpose") or {}).get("code")
        consent.hi_types, consent.care_contexts = detail.get("hiTypes") or [], detail.get("careContexts") or []
        if not problem:
            consent.date_from = parse_time(permission["dateRange"]["from"])
            consent.date_to = parse_time(permission["dateRange"]["to"])
            consent.data_erase_at = parse_time(permission["dataEraseAt"])
        consent.status, consent.status_reason = ("REJECTED", problem) if problem else ("GRANTED", None)
        consent.artefact = detail
        db.add(consent)
        if problem:
            response.update(error={"code": GENERIC_ERROR, "message": problem},
                            acknowledgement={"status": "FAILURE", "consentId": consent_id})
        else:
            response["acknowledgement"] = {"status": "OK", "consentId": consent_id}
        message = f"Consent refused: {problem}" if problem else f"Consent granted to {consent.hiu_id} for {', '.join(consent.hi_types)}"
    else:  # REVOKED, EXPIRED, DENIED: stop sharing immediately
        if consent:
            consent.status, consent.status_reason = status or "REVOKED", "Notified by the consent manager"
        response["acknowledgement"] = {"status": "OK", "consentId": consent_id}
        problem, message = None, f"Consent {consent_id} is now {status or 'REVOKED'}"
    tx = record(db, hospital.id, "consent_notify", "inbound", "failed" if problem else "pending", message,
                consent_id=consent_id, patient_id=consent.patient_id if consent else None,
                details={"status": status, "hiTypes": detail.get("hiTypes"), "hiu": (detail.get("hiu") or {}).get("id")})
    db.commit()
    http_status, data, _ = gateway.call(gateway.CONSENT_ON_NOTIFY_PATH, response, {"X-HIP-ID": gateway.hip_id()})
    if check_accepted(db, tx, http_status, data, message) and not problem:
        tx.status = "success"
    return tx


# ---------- health information request and transfer ----------

def health_information(db: Session, hospital: Hospital, headers: dict, body: dict) -> HipTransaction:
    transaction_id, hi_request = body.get("transactionId"), body.get("hiRequest") or {}
    consent_id = (hi_request.get("consent") or {}).get("id")
    consent = db.scalar(select(HipConsent).where(HipConsent.consent_id == consent_id))
    key_material = hi_request.get("keyMaterial") or {}
    problem = None
    try:
        start, end = (parse_time((hi_request.get("dateRange") or {}).get(k)) for k in ("from", "to"))
    except ValueError:
        start = end = None
    if consent is None or consent.status != "GRANTED":
        problem = "Consent not found or not active (granted)"
    elif consent.data_erase_at and consent.data_erase_at < utcnow():
        problem = "Consent EXPIRED: exceeded dataEraseAt"
    elif not (start and end) or start < consent.date_from or end > consent.date_to or start > end:
        problem = "Requested date range is outside the consented range"
    elif key_material.get("cryptoAlg") != crypto.CRYPTO_ALG or (key_material.get("curve") or "").lower() != crypto.CURVE.lower():
        problem = "Unsupported key material (expected ECDH on Curve25519)"
    elif not re.match(r"^https://" if not gateway.simulation() else r"^(https|sim)://", hi_request.get("dataPushUrl") or ""):
        problem = "dataPushUrl must be an https URL"
    else:
        try:
            receiver_key = crypto.unb64(key_material["dhPublicKey"]["keyValue"])
            receiver_nonce = crypto.unb64(key_material["nonce"])
            crypto.u_from_public_key(receiver_key)
            if len(receiver_nonce) != 32:
                raise crypto.CryptoError("nonce must be 32 bytes")
        except (KeyError, TypeError, crypto.CryptoError) as exc:
            problem = f"Invalid key material: {exc}"
    on_request = {"response": {"requestId": headers.get("request-id")}}
    if problem:
        on_request["error"] = {"code": GENERIC_ERROR, "message": problem}
    else:
        on_request["hiRequest"] = {"transactionId": transaction_id, "sessionStatus": "ACKNOWLEDGED"}
    tx = record(db, hospital.id, "health_information", "inbound", "failed" if problem else "pending",
                f"Request refused: {problem}" if problem else "Request acknowledged; preparing records",
                transaction_id=transaction_id, consent_id=consent_id, patient_id=consent.patient_id if consent else None,
                details={"dateRange": hi_request.get("dateRange"), "hiu": consent.hiu_id if consent else None})
    db.commit()
    status, data, _ = gateway.call(gateway.HI_ON_REQUEST_PATH, on_request, {"X-HIP-ID": gateway.hip_id()})
    check_accepted(db, tx, status, data, tx.message)
    if problem or tx.status == "failed":
        db.commit()
        return tx

    # Only care contexts in the consent, of consented HI types, validated (HIP-eligible) and dated inside both ranges.
    refs = [r["careContextReference"] for r in consent.care_contexts]
    contexts = db.scalars(select(CareContext).where(CareContext.reference.in_(refs))).all()
    own = crypto.generate_key_material()
    entries, statuses = [], []
    for c in contexts:
        reason = None
        if c.hi_type not in consent.hi_types:
            reason = "HI type not covered by the consent"
        elif not (start <= c.record_date <= end):
            reason = "Record date is outside the requested date range"
        elif not c.fhir_record.hip_eligible:
            reason = "Record is no longer validated for sharing"
        if reason:
            statuses.append({"careContextReference": c.reference, "hiStatus": "ERRORED", "description": reason})
            continue
        content = json.dumps(c.fhir_record.bundle, separators=(",", ":"))
        entries.append({"content": crypto.encrypt(content, own["private"], own["nonce"], receiver_key, receiver_nonce),
                        "media": "application/fhir+json",
                        "checksum": hashlib.md5(content.encode()).hexdigest(),  # MD5 of the FHIR content
                        "careContextReference": c.reference})
        statuses.append({"careContextReference": c.reference, "hiStatus": "DELIVERED", "description": "Done"})
    key_out = {"cryptoAlg": crypto.CRYPTO_ALG, "curve": crypto.CURVE,
               "dhPublicKey": {"expiry": key_material["dhPublicKey"].get("expiry"),
                               "parameters": key_material["dhPublicKey"].get("parameters") or crypto.PARAMETERS,
                               "keyValue": crypto.b64(own["x509"])},
               "nonce": crypto.b64(own["nonce"])}
    pages = [entries[i:i + PUSH_PAGE_SIZE] for i in range(0, len(entries), PUSH_PAGE_SIZE)] or [[]]
    pushed_ok = True
    for number, page in enumerate(pages):
        if not entries:
            break
        push_status = gateway.push(hi_request["dataPushUrl"], {"pageNumber": number, "pageCount": len(pages),
                                                               "transactionId": transaction_id, "entries": page,
                                                               "keyMaterial": key_out})
        pushed_ok = pushed_ok and 200 <= push_status < 300
    if not pushed_ok:
        statuses = [{**s, "hiStatus": "ERRORED", "description": "Data push to the HIU failed"}
                    if s["hiStatus"] == "DELIVERED" else s for s in statuses]
    delivered = sum(s["hiStatus"] == "DELIVERED" for s in statuses)
    session_status = "TRANSFERRED" if delivered and pushed_ok else "FAILED"
    notify = {"notification": {
        "consentId": consent_id, "transactionId": transaction_id, "doneAt": gateway.timestamp(),
        "notifier": {"type": "HIP", "id": gateway.hip_id()},
        "statusNotification": {"sessionStatus": session_status, "hipId": gateway.hip_id(), "statusResponses": statuses}}}
    status, data, _ = gateway.call(gateway.HI_NOTIFY_PATH, notify, {"X-HIP-ID": gateway.hip_id()})
    tx.details = {**(tx.details or {}), "statusResponses": statuses, "pages": len(pages) if entries else 0}
    tx.status = "success" if session_status == "TRANSFERRED" else "failed"
    tx.message = (f"{delivered} of {len(statuses)} record(s) encrypted and sent to {consent.hiu_id}"
                  if delivered else "No records could be shared for this request")
    if not 200 <= status < 300:
        tx.message += f"; status notification refused by ABDM (HTTP {status})"
    audit(db, None, "hip.data_transfer", "consent", consent_id,
          {"transaction_id": transaction_id, "delivered": delivered, "hiu": consent.hiu_id}, hospital_id=hospital.id)
    db.commit()
    return tx


# ---------- inbound dispatch ----------

INBOUND = {"discover": discover, "link_init": link_init, "link_confirm": link_confirm,
           "consent_notify": consent_notify, "health_information": health_information}
CALLBACKS = {"on_generate_token": on_generate_token, "on_add_care_contexts": on_add_care_contexts}


def handle_inbound(kind: str, headers: dict, body: dict) -> HipTransaction | None:
    """Process one authenticated ABDM callback (run after the 202 acknowledgement)."""
    headers = {k.lower(): v for k, v in headers.items()}
    with SessionLocal() as db:
        try:
            hospital = hip_hospital(db)
            if kind in CALLBACKS:
                message = CALLBACKS[kind](db, body)
                tx = record(db, hospital.id, kind, "inbound", "failed" if body.get("error") else "success", message,
                            request_id=(body.get("response") or {}).get("requestId"), details={"error": body.get("error")})
                db.commit()
                return tx
            tx = INBOUND[kind](db, hospital, headers, body)
            db.commit()
            return tx
        except (gateway.GatewayError, HipError) as exc:
            db.rollback()
            hospital_id = db.scalar(select(Hospital.id))
            tx = record(db, hospital_id, kind, "inbound", "failed", f"Could not complete: {exc}")
            db.commit()
            return tx

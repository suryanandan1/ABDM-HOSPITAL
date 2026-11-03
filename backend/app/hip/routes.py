"""HIP staff API (/api/hip/...) and ABDM callback endpoints (bridge paths /api/v3/...)."""

from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.fhir_validation import validator_available
from app.hip import gateway, service, simulator
from app.models import CareContext, HipConsent, HipLinkRequest, HipPatientLink, HipTransaction, Patient, User
from app.schemas import ORMModel, Page, PatientSummary, UTCDateTime
from app.security import require
from app.services import get_or_404, paginate

staff = APIRouter(prefix="/hip", tags=["hip"])
callbacks = APIRouter(tags=["abdm-callbacks"])


class CareContextOut(ORMModel):
    id: int
    patient_id: int
    reference: str
    display: str
    hi_type: str
    record_date: UTCDateTime
    link_status: str
    linked_abha_address: str | None
    link_method: str | None
    linked_at: UTCDateTime | None
    message: str | None
    fhir_record_id: int


class TransactionOut(ORMModel):
    id: int
    mode: str
    kind: str
    direction: str
    status: str
    message: str
    request_id: str | None
    transaction_id: str | None
    consent_id: str | None
    patient: PatientSummary | None
    http_status: int | None
    details: dict | None
    created_at: UTCDateTime


class ConsentOut(ORMModel):
    consent_id: str
    mode: str
    status: str
    status_reason: str | None
    patient: PatientSummary | None
    abha_address: str | None
    hiu_id: str | None
    purpose: str | None
    hi_types: list
    care_contexts: list
    date_from: UTCDateTime | None
    date_to: UTCDateTime | None
    data_erase_at: UTCDateTime | None
    created_at: UTCDateTime


def utc(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def after_change() -> None:
    """In simulation, deliver the gateway's callbacks once the HIP's own transaction is committed."""
    if gateway.simulation():
        simulator.flush()


def run(action):
    try:
        result = action()
    except service.HipError as exc:
        raise HTTPException(422, str(exc))
    except gateway.GatewayError as exc:
        raise HTTPException(502, exc.message)
    after_change()
    return result


# ---------- status ----------

@staff.get("/status")
def hip_status(user: User = Depends(require("hip:read")), db: Session = Depends(get_db)):
    sandbox = not gateway.simulation()
    checks = [
        {"label": "Mode", "ok": True, "detail": "Simulation: an in-process stand-in for ABDM (no real data leaves)"
         if not sandbox else f"ABDM sandbox at {settings.abdm_gateway_url} (X-CM-ID {settings.abdm_cm_id})"},
        {"label": "HIP ID", "ok": bool(gateway.hip_id()), "detail": gateway.hip_id() or "Set ABDM_HIP_ID (facility ID registered as HIP)"},
        {"label": "Client credentials", "ok": not sandbox or bool(settings.abdm_client_id and settings.abdm_client_secret),
         "detail": "Not needed in simulation" if not sandbox else
         ("Configured" if settings.abdm_client_id and settings.abdm_client_secret else "Set ABDM_CLIENT_ID and ABDM_CLIENT_SECRET")},
        {"label": "Bridge URL", "ok": not sandbox or (settings.abdm_bridge_url or "").startswith("https://"),
         "detail": settings.abdm_bridge_url or ("Not needed in simulation" if not sandbox else "Set ABDM_BRIDGE_URL to a public https URL")},
        {"label": "Callback verification", "ok": settings.abdm_verify_callbacks,
         "detail": "Gateway JWTs are verified against ABDM's published keys" if settings.abdm_verify_callbacks
         else "DISABLED: callbacks are not authenticated"},
        {"label": "Patient OTP delivery", "ok": not sandbox or settings.abdm_otp_test_display,
         "detail": "Simulated SMS" if not sandbox else ("Test display only (no SMS gateway)" if settings.abdm_otp_test_display
                                                         else "No SMS gateway: patient-initiated linking will be refused")},
        {"label": "FHIR validator", "ok": validator_available(), "detail": "Only validated FHIR records become care contexts"},
    ]
    hid = user.hospital_id

    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(model.hospital_id == hid, *where))

    counts = {
        "verified_links": count(HipPatientLink, HipPatientLink.status == "verified"),
        "care_contexts": count(CareContext), "linked_care_contexts": count(CareContext, CareContext.link_status == "linked"),
        "discovery_requests": count(HipTransaction, HipTransaction.kind == "discover"),
        "active_consents": count(HipConsent, HipConsent.status == "GRANTED"),
        "completed_transfers": count(HipTransaction, HipTransaction.kind == "health_information", HipTransaction.status == "success"),
        "pending_transfers": count(HipTransaction, HipTransaction.kind == "health_information", HipTransaction.status == "pending"),
        "failed": count(HipTransaction, HipTransaction.status == "failed"),
    }
    return {"mode": settings.abdm_mode, "hip_id": gateway.hip_id(), "checks": checks, "counts": counts,
            "ready": all(c["ok"] for c in checks if c["label"] != "FHIR validator")}


@staff.post("/session-check")
def session_check(user: User = Depends(require("hip:configure")), db: Session = Depends(get_db)):
    """Obtain a gateway session token (proves the client credentials work); the token is not returned."""
    try:
        gateway.reset()
        gateway.session_token()
        status, message = "success", "Gateway session created with the configured credentials"
    except gateway.GatewayError as exc:
        status, message = "failed", exc.message
    tx = service.record(db, user.hospital_id, "session", "outbound", status, message)
    db.commit()
    return {"status": status, "message": message, "transaction_id": tx.id}


@staff.post("/bridge-url")
def update_bridge_url(user: User = Depends(require("hip:configure")), db: Session = Depends(get_db)):
    if not (settings.abdm_bridge_url or "").startswith("https://") and not gateway.simulation():
        raise HTTPException(422, "Set ABDM_BRIDGE_URL to the public https URL of this server first")
    url = settings.abdm_bridge_url or "https://hip.simulated.local"
    status, data, request_id = gateway.call(gateway.BRIDGE_URL_PATH, {"url": url}, method="PATCH")
    ok = 200 <= status < 300
    tx = service.record(db, user.hospital_id, "bridge_url", "outbound", "success" if ok else "failed",
                        f"Bridge URL set to {url}" if ok else f"ABDM refused the bridge URL (HTTP {status})",
                        request_id=request_id, http_status=status)
    db.commit()
    return {"status": tx.status, "message": tx.message}


# ---------- patients and care contexts ----------

@staff.get("/patients/{patient_id}")
def patient_overview(patient_id: int, user: User = Depends(require("hip:read")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    contexts = service.sync_care_contexts(db, patient)
    db.commit()
    link = db.scalar(select(HipPatientLink).where(HipPatientLink.patient_id == patient.id))
    show_otp = gateway.simulation() or settings.abdm_otp_test_display
    requests = db.scalars(select(HipLinkRequest).where(HipLinkRequest.patient_id == patient.id)
                          .order_by(HipLinkRequest.id.desc()).limit(5)).all()
    return {
        "patient": PatientSummary.model_validate(patient).model_dump(),
        "abha_verification_method": patient.abha_verification_method,
        "link": link and {"abha_address": link.abha_address, "status": link.status, "message": link.message,
                          "mode": link.mode, "updated_at": utc(link.updated_at)},
        "care_contexts": [CareContextOut.model_validate(c).model_dump() for c in contexts],
        "link_requests": [{"link_reference": r.link_reference, "status": r.status, "abha_address": r.abha_address,
                           "care_contexts": r.care_context_refs, "mode": r.mode, "expires_at": utc(r.otp_expires_at),
                           "test_otp": r.test_otp if show_otp and r.status == "otp_sent" else None} for r in requests],
        "consents": [ConsentOut.model_validate(c).model_dump() for c in db.scalars(
            select(HipConsent).where(HipConsent.patient_id == patient.id).order_by(HipConsent.id.desc()))],
    }


@staff.post("/patients/{patient_id}/link-token")
def request_link_token(patient_id: int, user: User = Depends(require("hip:manage")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    tx = run(lambda: service.request_link_token(db, user, patient))
    return {"status": tx.status, "message": tx.message}


class LinkSelection(BaseModel):
    care_context_ids: list[int]


@staff.post("/patients/{patient_id}/care-contexts/link")
def link_care_contexts(patient_id: int, body: LinkSelection, user: User = Depends(require("hip:manage")),
                       db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    contexts = db.scalars(select(CareContext).where(CareContext.id.in_(body.care_context_ids),
                                                    CareContext.patient_id == patient.id)).all()
    if len(contexts) != len(set(body.care_context_ids)) or any(c.link_status == "linked" for c in contexts):
        raise HTTPException(422, "Select unlinked care contexts of this patient")
    tx = run(lambda: service.link_care_contexts(db, user, patient, contexts))
    return {"status": tx.status, "message": tx.message}


@staff.get("/care-contexts", response_model=Page[CareContextOut])
def list_care_contexts(link_status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: User = Depends(require("hip:read")), db: Session = Depends(get_db)):
    query = select(CareContext).where(CareContext.hospital_id == user.hospital_id).order_by(CareContext.id.desc())
    if link_status:
        query = query.where(CareContext.link_status == link_status)
    return paginate(db, query, page, page_size)


@staff.get("/consents", response_model=Page[ConsentOut])
def list_consents(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  user: User = Depends(require("hip:read")), db: Session = Depends(get_db)):
    query = select(HipConsent).where(HipConsent.hospital_id == user.hospital_id).order_by(HipConsent.id.desc())
    if status:
        query = query.where(HipConsent.status == status)
    return paginate(db, query, page, page_size)


@staff.get("/transactions", response_model=Page[TransactionOut])
def list_transactions(kind: str | None = None, status: str | None = None, page: int = Query(1, ge=1),
                      page_size: int = Query(25, ge=1, le=100), user: User = Depends(require("hip:read")),
                      db: Session = Depends(get_db)):
    query = select(HipTransaction).where(HipTransaction.hospital_id == user.hospital_id).order_by(HipTransaction.id.desc())
    if kind:
        query = query.where(HipTransaction.kind.in_(kind.split(",")))
    if status:
        query = query.where(HipTransaction.status == status)
    return paginate(db, query, page, page_size)


# ---------- simulation (ABDM_MODE=simulation only) ----------

def simulation_only(user: User = Depends(require("hip:manage"))) -> User:
    if not gateway.simulation():
        raise HTTPException(409, "Simulation actions are disabled in sandbox mode")
    return user


class SimAbha(BaseModel):
    abha_address: str
    name: str
    gender: str
    year_of_birth: int
    abha_number: str | None = None


@staff.post("/simulate/abha")
def simulate_abha(body: SimAbha, _: User = Depends(simulation_only)):
    simulator.registry[body.abha_address.lower()] = {"name": body.name, "gender": body.gender,
                                                     "yearOfBirth": body.year_of_birth, "abhaNumber": body.abha_number}
    return {"message": f"Simulated ABHA account {body.abha_address} created"}


@staff.post("/simulate/discover/{patient_id}")
def simulate_discover(patient_id: int, abha_address: str = Body(embed=True), user: User = Depends(simulation_only),
                      db: Session = Depends(get_db)):
    p = get_or_404(db, Patient, patient_id, user.hospital_id)
    return simulator.discover(abha_address.lower(), p.full_name, p.gender, p.year_of_birth, p.mobile)


class SimLink(BaseModel):
    transaction_id: str
    abha_address: str
    patient: list[dict]


@staff.post("/simulate/link-init")
def simulate_link_init(body: SimLink, user: User = Depends(simulation_only), db: Session = Depends(get_db)):
    reply = simulator.link_init(body.transaction_id, body.abha_address.lower(), body.patient)
    request = db.scalar(select(HipLinkRequest).where(HipLinkRequest.link_reference == ((reply or {}).get("link") or {}).get("referenceNumber")))
    return {"response": reply, "sms_to_patient": simulator.phone.get(db.get(Patient, request.patient_id).mobile) if request else None}


class SimConfirm(BaseModel):
    link_reference: str
    otp: str


@staff.post("/simulate/link-confirm")
def simulate_link_confirm(body: SimConfirm, _: User = Depends(simulation_only)):
    return simulator.link_confirm(body.link_reference, body.otp)


class SimConsent(BaseModel):
    patient_id: int
    care_context_ids: list[int] | None = None  # default: all linked care contexts
    hi_types: list[str] | None = None
    date_from: date
    date_to: date
    erase_at: datetime
    status: str = "GRANTED"
    consent_id: str | None = None


@staff.post("/simulate/consent")
def simulate_consent(body: SimConsent, user: User = Depends(simulation_only), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, body.patient_id, user.hospital_id)
    contexts = [c for c in db.scalars(select(CareContext).where(CareContext.patient_id == patient.id))
                if c.link_status == "linked" and (body.care_context_ids is None or c.id in body.care_context_ids)]
    if not contexts and body.status == "GRANTED":
        raise HTTPException(422, "The patient has no linked care contexts to consent to")
    return simulator.grant_consent(contexts[0].linked_abha_address if contexts else patient.abha_address or "",
                                   patient.patient_code, [c.reference for c in contexts],
                                   body.hi_types or sorted({c.hi_type for c in contexts}),
                                   f"{body.date_from}T00:00:00.000Z", f"{body.date_to}T23:59:59.000Z",
                                   body.erase_at.isoformat() + ("Z" if body.erase_at.tzinfo is None else ""),
                                   body.status, body.consent_id)


class SimRequest(BaseModel):
    consent_id: str
    date_from: date
    date_to: date


@staff.post("/simulate/health-information")
def simulate_health_information(body: SimRequest, _: User = Depends(simulation_only)):
    return simulator.request_health_information(body.consent_id, f"{body.date_from}T00:00:00.000Z",
                                                f"{body.date_to}T23:59:59.000Z")


# ---------- ABDM callbacks (bridge URL + these paths) ----------

REQUIRED = {
    "discover": ["transactionId", "patient"],
    "link_init": ["transactionId", "abhaAddress", "patient"],
    "link_confirm": ["confirmation.linkRefNumber", "confirmation.token"],
    "consent_notify": ["notification.consentId", "notification.status"],
    "health_information": ["transactionId", "hiRequest.consent.id", "hiRequest.dataPushUrl", "hiRequest.keyMaterial"],
    "on_generate_token": ["response.requestId"],
    "on_add_care_contexts": ["response.requestId"],
}
HIP_ADDRESSED = {"discover", "link_init", "link_confirm", "consent_notify", "health_information"}


def accept(kind: str, required: list[str] | None = None, handler=None, id_header: str | None = None, expected_id=None):
    """A callback endpoint: verify the gateway JWT and the addressed HIP/HIU id, check required fields, answer 202
    and process afterwards. Shared by the HIP and HIU callbacks."""
    required = REQUIRED[kind] if required is None else required
    handler = handler or service.handle_inbound
    if id_header is None and kind in HIP_ADDRESSED:
        id_header, expected_id = "x-hip-id", gateway.hip_id

    async def endpoint(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Body must be JSON")
        try:
            gateway.verify_callback(request.headers.get("authorization"))
            if id_header and request.headers.get(id_header) != expected_id():
                raise gateway.CallbackAuthError(f"{id_header.upper()} does not match this facility")
        except gateway.CallbackAuthError as exc:
            try:
                service.record(db, service.hip_hospital(db).id, "callback_rejected", "inbound", "failed",
                               f"{kind}: {exc}", request_id=request.headers.get("request-id"))
                db.commit()
            except service.HipError:
                pass
            raise HTTPException(401, "Callback authentication failed")
        for path in required:
            node = body
            for part in path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node in (None, "", []):
                raise HTTPException(400, f"Missing {path}")
        background.add_task(handler, kind, dict(request.headers), body)
        return None
    return endpoint


for kind, path in {
    "discover": "/v3/hip/patient/care-context/discover",
    "link_init": "/v3/hip/link/care-context/init",
    "link_confirm": "/v3/hip/link/care-context/confirm",
    "consent_notify": "/v3/consent/request/hip/notify",
    "health_information": "/v3/hip/health-information/request",
    "on_generate_token": "/v3/hip/token/on-generate-token",
    "on_add_care_contexts": "/v3/link/on_carecontext",
}.items():
    callbacks.add_api_route(path, accept(kind), methods=["POST"], status_code=202, name=f"abdm_{kind}")

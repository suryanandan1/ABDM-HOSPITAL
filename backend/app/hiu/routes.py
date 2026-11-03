"""HIU staff API (/api/hiu/...), the data push endpoint and the HIU callbacks (/api/v3/hiu/...)."""

import json
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import AIError
from app.config import settings
from app.database import get_db
from app.hip import gateway
from app.hip.routes import accept, after_change, utc
from app.hiu import ai as hiu_ai, records, service, simulator
from app.models import (Doctor, Encounter, HipTransaction, HiuConsentArtefact, HiuConsentRequest, HiuDataRequest,
                        HiuPatientSummary, Patient, Prescription, RetrievedRecord, User, utcnow)
from app.schemas import ORMModel, Page, PatientSummary, UTCDateTime
from app.security import require
from app.services import audit, current_doctor, get_or_404, paginate

staff = APIRouter(prefix="/hiu", tags=["hiu"])
push = APIRouter(tags=["hiu-data-push"])
callbacks = APIRouter(tags=["abdm-callbacks"])


class RequestOut(ORMModel):
    id: int
    patient: PatientSummary
    abha_address: str
    purpose_code: str
    hi_types: list
    date_from: UTCDateTime
    date_to: UTCDateTime
    data_erase_at: UTCDateTime
    reason: str | None
    status: str
    status_message: str | None
    consent_request_id: str | None
    mode: str
    created_at: UTCDateTime
    updated_at: UTCDateTime


class RecordOut(ORMModel):
    id: int
    patient_id: int
    source_hip_id: str | None
    source_name: str | None
    category: str
    record_type: str | None
    record_date: UTCDateTime | None
    received_at: UTCDateTime
    consent_artefact_id: str
    care_context_reference: str
    checksum_ok: bool
    validation_status: str
    erase_at: UTCDateTime
    mode: str


def run(action):
    try:
        result = action()
    except service.HiuError as exc:
        raise HTTPException(422, str(exc))
    except gateway.GatewayError as exc:
        raise HTTPException(502, exc.message)
    after_change()
    return result


# ---------- dashboard and request preparation ----------

@staff.get("/dashboard")
def dashboard(user: User = Depends(require("hiu:read")), db: Session = Depends(get_db)):
    service.enforce_retention(db)
    rows = db.execute(select(HiuConsentRequest.status, func.count()).where(HiuConsentRequest.hospital_id == user.hospital_id)
                      .group_by(HiuConsentRequest.status)).all()
    counts = {status: n for status, n in rows}
    today = datetime.combine(date.today(), time.min)
    return {
        "mode": settings.abdm_mode, "hiu_id": gateway.hiu_id(),
        "counts": {"new_today": db.scalar(select(func.count()).select_from(HiuConsentRequest).where(
                       HiuConsentRequest.hospital_id == user.hospital_id, HiuConsentRequest.created_at >= today)),
                   "pending_consent": counts.get("pending_consent", 0) + counts.get("requested", 0),
                   "granted": counts.get("granted", 0), "denied": counts.get("denied", 0),
                   "retrieving": counts.get("retrieving", 0), "received": counts.get("received", 0),
                   "failed": counts.get("failed", 0) + counts.get("expired", 0) + counts.get("revoked", 0),
                   "records": db.scalar(select(func.count()).select_from(RetrievedRecord).where(
                       RetrievedRecord.hospital_id == user.hospital_id))},
        "purposes": hiu_ai.PURPOSES, "hi_types": hiu_ai.HI_TYPES,
    }


class SuggestIn(BaseModel):
    patient_id: int
    reason: str = Field(min_length=3, max_length=500)


@staff.post("/suggest")
def suggest(body: SuggestIn, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    """AI suggestion for purpose, categories and dates. Nothing is created or sent."""
    patient = get_or_404(db, Patient, body.patient_id, user.hospital_id)
    encounters = db.scalars(select(Encounter).where(Encounter.patient_id == patient.id, Encounter.status == "approved")
                            .order_by(Encounter.visit_at.desc()).limit(5)).all()
    prescriptions = db.scalars(select(Prescription).where(Prescription.patient_id == patient.id,
                                                          Prescription.status == "approved").limit(5)).all()
    context = {"today": date.today().isoformat(), "clinical_reason": body.reason,
               "patient": {"age_years": date.today().year - patient.year_of_birth, "gender": patient.gender},
               "this_hospital_records": {
                   "recent_visits": [{"date": e.visit_at.date().isoformat(), "chief_complaint": e.chief_complaint,
                                      "diagnoses": [c.display for c in e.conditions]} for e in encounters],
                   "medicines": sorted({i.medicine_name for p in prescriptions for i in p.items})},
               "allowed_purposes": hiu_ai.PURPOSES, "allowed_hi_types": hiu_ai.HI_TYPES}
    try:
        suggestion = hiu_ai.suggest_request(context)
    except AIError as exc:
        raise HTTPException(503, f"AI suggestions unavailable: {exc}. Fill in the request manually.")
    audit(db, user, "hiu.ai_suggestion", "patient", patient.id, {"purpose": suggestion["purpose_code"]})
    db.commit()
    return suggestion


class RequestIn(BaseModel):
    patient_id: int
    requester_doctor_id: int | None = None
    purpose_code: str
    hi_types: list[str]
    date_from: date
    date_to: date
    erase_at: date | None = None
    reason: str | None = Field(None, max_length=500)
    ai_suggestion: dict | None = None


@staff.post("/requests", response_model=RequestOut, status_code=201)
def create_request(body: RequestIn, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    """Staff submit the request (possibly after reviewing AI suggestions). The patient decides on consent."""
    patient = get_or_404(db, Patient, body.patient_id, user.hospital_id)
    doctor = get_or_404(db, Doctor, body.requester_doctor_id, user.hospital_id) if body.requester_doctor_id \
        else current_doctor(db, user)
    erase_at = datetime.combine(body.erase_at or date.today() + timedelta(days=30), time.max.replace(microsecond=0))
    request = run(lambda: service.create_request(
        db, user, patient, doctor, body.purpose_code, body.hi_types, datetime.combine(body.date_from, time.min),
        datetime.combine(body.date_to, time.max.replace(microsecond=0)), erase_at, body.reason, body.ai_suggestion))
    db.refresh(request)  # acknowledgements may already have arrived
    return request


@staff.get("/requests", response_model=Page[RequestOut])
def list_requests(status: str | None = None, patient_id: int | None = None, page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100), user: User = Depends(require("hiu:read")),
                  db: Session = Depends(get_db)):
    service.enforce_retention(db)
    query = select(HiuConsentRequest).where(HiuConsentRequest.hospital_id == user.hospital_id) \
        .order_by(HiuConsentRequest.id.desc())
    if status:
        query = query.where(HiuConsentRequest.status.in_(status.split(",")))
    if patient_id:
        query = query.where(HiuConsentRequest.patient_id == patient_id)
    return paginate(db, query, page, page_size)


@staff.get("/requests/{request_id}")
def get_request(request_id: int, user: User = Depends(require("hiu:read")), db: Session = Depends(get_db)):
    request = get_or_404(db, HiuConsentRequest, request_id, user.hospital_id)
    artefact_ids = [a.artefact_id for a in request.artefacts]
    data_requests = db.scalars(select(HiuDataRequest).where(
        HiuDataRequest.artefact_id.in_([a.id for a in request.artefacts])).order_by(HiuDataRequest.id)).all()
    transactions = db.scalars(select(HipTransaction).where(
        (HipTransaction.request_id.in_([request.request_id] + [a.fetch_request_id for a in request.artefacts if a.fetch_request_id]
                                       + [d.request_id for d in data_requests]))
        | HipTransaction.consent_id.in_(artefact_ids + ([request.consent_request_id] if request.consent_request_id else [])))
        .order_by(HipTransaction.id)).all()
    return {
        "request": RequestOut.model_validate(request).model_dump(),
        "requester": request.requester_doctor.full_name, "ai_suggestion": request.ai_suggestion,
        "artefacts": [{"id": a.id, "artefact_id": a.artefact_id, "status": a.status, "status_message": a.status_message,
                       "hip_id": a.hip_id, "hi_types": a.hi_types, "care_contexts": len(a.care_contexts or []),
                       "date_from": utc(a.date_from), "date_to": utc(a.date_to), "data_erase_at": utc(a.data_erase_at)}
                      for a in request.artefacts],
        "data_requests": [{"id": d.id, "artefact_id": d.artefact.artefact_id, "status": d.status,
                           "status_message": d.status_message, "transaction_id": d.transaction_id,
                           "pages": f"{d.pages_received}/{d.pages_expected or '?'}", "created_at": utc(d.created_at),
                           "expires_at": utc(d.expires_at)} for d in data_requests],
        "timeline": [{"kind": t.kind, "direction": t.direction, "status": t.status, "message": t.message,
                      "mode": t.mode, "at": utc(t.created_at)} for t in transactions],
    }


@staff.post("/requests/{request_id}/check-status")
def check_status(request_id: int, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    request = get_or_404(db, HiuConsentRequest, request_id, user.hospital_id)
    return {"message": run(lambda: service.check_status(db, request))}


@staff.post("/artefacts/{artefact_id}/fetch")
def refetch(artefact_id: int, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    artefact = db.get(HiuConsentArtefact, artefact_id)
    if artefact is None or artefact.consent_request.hospital_id != user.hospital_id:
        raise HTTPException(404, "Artefact not found")
    if artefact.status not in ("notified", "invalid"):
        raise HTTPException(409, "This artefact does not need to be fetched again")
    run(lambda: service.fetch_artefact(db, artefact))
    return {"message": "Artefact fetch requested"}


@staff.post("/artefacts/{artefact_id}/request-data")
def retry_data(artefact_id: int, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    """Retry a health-information request under the same, still valid consent (new keys, new push URL)."""
    artefact = db.get(HiuConsentArtefact, artefact_id)
    if artefact is None or artefact.consent_request.hospital_id != user.hospital_id:
        raise HTTPException(404, "Artefact not found")
    service.enforce_retention(db)
    data_request = run(lambda: service.request_data(db, artefact))
    audit(db, user, "hiu.data_request_retry", "patient", artefact.consent_request.patient_id, {"consent": artefact.artefact_id})
    db.commit()
    return {"message": data_request.status_message}


# ---------- retrieved records (clinical data: hiu:records, every view audited) ----------

def visible_records(db: Session, user: User, patient_id: int) -> list[RetrievedRecord]:
    service.enforce_retention(db)
    return db.scalars(select(RetrievedRecord).where(RetrievedRecord.hospital_id == user.hospital_id,
                                                    RetrievedRecord.patient_id == patient_id)
                      .order_by(RetrievedRecord.record_date.desc())).all()


@staff.get("/patients/{patient_id}/records")
def patient_records(patient_id: int, request: Request, user: User = Depends(require("hiu:records")),
                    db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    rows = visible_records(db, user, patient.id)
    facts = [(r.id, records.describe(records.bundle_json(r))) for r in rows]
    summary = db.scalar(select(HiuPatientSummary).where(HiuPatientSummary.patient_id == patient.id))
    audit(db, user, "hiu.records_view", "patient", patient.id, {"records": [r.id for r in rows]}, request)
    db.commit()
    return {"patient": PatientSummary.model_validate(patient).model_dump(),
            "records": [RecordOut.model_validate(r).model_dump() for r in rows],
            "timeline": records.timeline(facts), "lab_trends": records.lab_trends(facts),
            "summary": summary and {**json.loads(records.unseal(summary.content)), "model": summary.model,
                                    "generated_at": summary.generated_at.isoformat() + "Z",
                                    "outdated": set(summary.record_ids) != {r.id for r in rows}}}


@staff.get("/records/{record_id}")
def get_record(record_id: int, request: Request, user: User = Depends(require("hiu:records")), db: Session = Depends(get_db)):
    service.enforce_retention(db)
    row = get_or_404(db, RetrievedRecord, record_id, user.hospital_id)
    bundle = records.bundle_json(row)
    audit(db, user, "hiu.record_view", "retrieved_record", row.id, {"consent": row.consent_artefact_id}, request)
    db.commit()
    return {"record": RecordOut.model_validate(row).model_dump(), "facts": records.describe(bundle),
            "validation_issues": row.validation_issues, "original": bundle}


@staff.post("/records/{record_id}/validate")
def validate(record_id: int, user: User = Depends(require("hiu:records")), db: Session = Depends(get_db)):
    row = get_or_404(db, RetrievedRecord, record_id, user.hospital_id)
    service.validate_record(row.id)
    db.refresh(row)
    return RecordOut.model_validate(row).model_dump()


@staff.post("/patients/{patient_id}/summary")
def summarize(patient_id: int, request: Request, user: User = Depends(require("hiu:records")), db: Session = Depends(get_db)):
    patient = get_or_404(db, Patient, patient_id, user.hospital_id)
    rows = visible_records(db, user, patient.id)
    if not rows:
        raise HTTPException(422, "No retrieved records to summarise")
    facts = [{"record_id": r.id, "source": r.source_name, **records.describe(records.bundle_json(r))} for r in rows]
    try:
        summary = hiu_ai.summarize(facts)
    except AIError as exc:
        raise HTTPException(503, f"AI summary unavailable: {exc}")
    row = db.scalar(select(HiuPatientSummary).where(HiuPatientSummary.patient_id == patient.id)) or \
        HiuPatientSummary(patient_id=patient.id)
    row.content, row.record_ids, row.model, row.generated_at = records.seal(json.dumps(summary)), [r.id for r in rows], \
        settings.ai_model, utcnow()
    db.add(row)
    audit(db, user, "hiu.ai_summary", "patient", patient.id, {"records": row.record_ids}, request)
    db.commit()
    return {**summary, "model": row.model, "generated_at": row.generated_at.isoformat() + "Z", "outdated": False}


# ---------- simulation: the patient's decision (ABDM_MODE=simulation only) ----------

class Decision(BaseModel):
    grant: bool
    hi_types: list[str] | None = None
    reason: str | None = None


@staff.post("/simulate/requests/{request_id}/decide")
def simulate_decision(request_id: int, body: Decision, user: User = Depends(require("hiu:request")),
                      db: Session = Depends(get_db)):
    if not gateway.simulation():
        raise HTTPException(409, "Simulation actions are disabled in sandbox mode")
    request = get_or_404(db, HiuConsentRequest, request_id, user.hospital_id)
    if request.status != "pending_consent":
        raise HTTPException(409, "The request is not waiting for consent")
    try:
        simulator.decide(request.consent_request_id, body.grant, body.hi_types, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.refresh(request)
    return RequestOut.model_validate(request).model_dump()


@staff.post("/simulate/requests/{request_id}/revoke")
def simulate_revoke(request_id: int, user: User = Depends(require("hiu:request")), db: Session = Depends(get_db)):
    if not gateway.simulation():
        raise HTTPException(409, "Simulation actions are disabled in sandbox mode")
    request = get_or_404(db, HiuConsentRequest, request_id, user.hospital_id)
    simulator.revoke(request.consent_request_id)
    db.refresh(request)
    return RequestOut.model_validate(request).model_dump()


# ---------- data push from HIPs and ABDM callbacks ----------

@push.post("/hiu/data-push/{token}", status_code=202)
async def data_push(token: str, request: Request, db: Session = Depends(get_db)):
    """HIPs post encrypted data here; the unguessable token identifies one open data request."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Body must be JSON")
    status, message, _ = service.receive_push(db, token, body)
    if status != 202:
        raise HTTPException(status, message)
    return {"message": message}


HIU_REQUIRED = {"hiu_on_init": ["response.requestId"], "hiu_on_status": ["response.requestId"],
                "hiu_notify": ["notification.consentRequestId", "notification.status"],
                "hiu_on_fetch": ["response.requestId"], "hiu_on_request": ["response.requestId"]}
for kind, path in {"hiu_on_init": "/v3/hiu/consent/request/on-init", "hiu_on_status": "/v3/hiu/consent/request/on-status",
                   "hiu_notify": "/v3/hiu/consent/request/notify", "hiu_on_fetch": "/v3/hiu/consent/on-fetch",
                   "hiu_on_request": "/v3/hiu/health-information/on-request"}.items():
    callbacks.add_api_route(path, accept(kind, HIU_REQUIRED[kind], service.handle_inbound, "x-hiu-id", gateway.hiu_id),
                            methods=["POST"], status_code=202, name=f"abdm_{kind}")

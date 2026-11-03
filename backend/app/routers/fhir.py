"""FHIR document generation, validation and download for approved records."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import fhir, fhir_sources, fhir_validation
from app.database import get_db
from app.models import ClinicalDocument, Encounter, FhirRecord, LabOrder, Prescription, User, utcnow
from app.schemas import FhirGenerate, FhirRecordOut, FhirRecordSummary, Page
from app.security import require
from app.services import audit, get_or_404, paginate

router = APIRouter(prefix="/fhir", tags=["fhir"])

NOT_APPROVED = "Only approved records can be converted to FHIR"


def load_source(db: Session, user: User, source_type: str, source_id: int) -> fhir_sources.Source:
    """Load an approved record and convert it to engine input."""
    if source_type == "consultation":
        e = get_or_404(db, Encounter, source_id, user.hospital_id)
        if e.status != "approved":
            raise HTTPException(409, NOT_APPROVED)
        prescriptions = db.scalars(select(Prescription).where(
            Prescription.encounter_id == e.id, Prescription.status == "approved")).all()
        orders = db.scalars(select(LabOrder).where(LabOrder.encounter_id == e.id, LabOrder.status != "cancelled")).all()
        return fhir_sources.consultation_source(e, prescriptions, orders)
    if source_type == "prescription":
        p = get_or_404(db, Prescription, source_id, user.hospital_id)
        if p.status != "approved":
            raise HTTPException(409, NOT_APPROVED)
        return fhir_sources.prescription_source(p)
    if source_type == "lab_order":
        o = get_or_404(db, LabOrder, source_id, user.hospital_id)
        if o.report is None or o.report.status != "approved":
            raise HTTPException(409, NOT_APPROVED)
        return fhir_sources.lab_order_source(o)
    doc = get_or_404(db, ClinicalDocument, source_id, user.hospital_id)
    if doc.status != "approved":
        raise HTTPException(409, NOT_APPROVED)
    return fhir_sources.document_source(doc)


def build(source: fhir_sources.Source, user: User, version: int = 1) -> dict:
    if source.author is None:
        raise HTTPException(422, "The record has no responsible doctor, so it cannot be authored in FHIR")
    return fhir.build_bundle(source.content, source.patient, user.hospital, source.author, source.doctors, version)


def store_record(db: Session, user: User, source_type: str, source_id: int, request: Request | None = None) -> FhirRecord:
    """Generate (or regenerate) and save the Bundle; any earlier validation no longer applies."""
    source = load_source(db, user, source_type, source_id)
    record = db.scalar(select(FhirRecord).where(FhirRecord.source_type == source_type, FhirRecord.source_id == source_id))
    version = record.version + 1 if record else 1
    bundle = build(source, user, version)
    record = record or FhirRecord(hospital_id=user.hospital_id, source_type=source_type, source_id=source_id)
    record.patient_id, record.record_type = source.patient.id, source.content.record_type
    record.title, record.version, record.bundle = source.content.title, version, bundle
    record.prechecks = fhir_validation.prechecks(bundle, source.warnings)
    record.validation_status, record.validation_issues, record.validator, record.validated_at = "not_validated", None, None, None
    record.generated_by_id, record.generated_at = user.id, utcnow()
    db.add(record)
    db.flush()
    audit(db, user, "fhir.generate", "fhir_record", record.id,
          {"source": f"{source_type}/{source_id}", "record_type": record.record_type, "version": version}, request)
    return record


def fhir_response(bundle: dict, filename: str | None = None) -> JSONResponse:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else None
    return JSONResponse(bundle, media_type="application/fhir+json", headers=headers)


# Phase 2 preview URLs, now served by the engine (nothing is stored).
@router.get("/consultations/{source_id}")
def op_consult_record(source_id: int, user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    return fhir_response(build(load_source(db, user, "consultation", source_id), user))


@router.get("/prescriptions/{source_id}")
def prescription_record(source_id: int, user: User = Depends(require("clinical:read")), db: Session = Depends(get_db)):
    return fhir_response(build(load_source(db, user, "prescription", source_id), user))


@router.get("/lab-orders/{source_id}")
def diagnostic_report_record(source_id: int, user: User = Depends(require("lab:read")), db: Session = Depends(get_db)):
    return fhir_response(build(load_source(db, user, "lab_order", source_id), user))


# ---------- stored records ----------

@router.get("/records", response_model=Page[FhirRecordSummary])
def list_records(patient_id: int | None = None, validation_status: str | None = None, source_type: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 user: User = Depends(require("fhir:read")), db: Session = Depends(get_db)):
    query = select(FhirRecord).where(FhirRecord.hospital_id == user.hospital_id).order_by(FhirRecord.generated_at.desc())
    for column, value in ((FhirRecord.patient_id, patient_id), (FhirRecord.validation_status, validation_status),
                          (FhirRecord.source_type, source_type)):
        if value:
            query = query.where(column == value)
    return paginate(db, query, page, page_size)


@router.post("/records", response_model=FhirRecordOut, status_code=201)
def generate_record(body: FhirGenerate, request: Request, user: User = Depends(require("fhir:generate")),
                    db: Session = Depends(get_db)):
    record = store_record(db, user, body.source_type, body.source_id, request)
    db.commit()
    return record


@router.get("/records/{record_id}", response_model=FhirRecordOut)
def get_record(record_id: int, user: User = Depends(require("fhir:read")), db: Session = Depends(get_db)):
    return get_or_404(db, FhirRecord, record_id, user.hospital_id)


@router.post("/records/{record_id}/regenerate", response_model=FhirRecordOut)
def regenerate_record(record_id: int, request: Request, user: User = Depends(require("fhir:generate")),
                      db: Session = Depends(get_db)):
    record = get_or_404(db, FhirRecord, record_id, user.hospital_id)
    record = store_record(db, user, record.source_type, record.source_id, request)
    db.commit()
    return record


@router.post("/records/{record_id}/validate", response_model=FhirRecordOut)
def validate_record(record_id: int, request: Request, user: User = Depends(require("fhir:generate")),
                    db: Session = Depends(get_db)):
    """Run the official HL7 validator (takes about a minute)."""
    record = get_or_404(db, FhirRecord, record_id, user.hospital_id)
    status, issues, validator = fhir_validation.validate(record.bundle)
    record.validation_status, record.validation_issues = status, issues
    record.validator, record.validated_at = validator, utcnow()
    audit(db, user, "fhir.validate", "fhir_record", record.id,
          {"status": status, "errors": sum(i["severity"] == "error" for i in issues), "version": record.version}, request)
    db.commit()
    return record


@router.get("/records/{record_id}/download")
def download_record(record_id: int, request: Request, user: User = Depends(require("fhir:read")),
                    db: Session = Depends(get_db)):
    record = get_or_404(db, FhirRecord, record_id, user.hospital_id)
    audit(db, user, "fhir.download", "fhir_record", record.id, {"version": record.version}, request)
    db.commit()
    return fhir_response(record.bundle, f"{record.record_type}-{record.source_type}-{record.source_id}-v{record.version}.json")

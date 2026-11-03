"""Clinical document upload, AI extraction, human review, LOINC confirmation and professional approval."""

import logging
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import loinc
from app.config import settings
from app.database import SessionLocal, get_db
from app.extraction import (DOCX_TYPE, ExtractionError, LoincMapping, ReviewedRecord, blank_record, document_text,
                            extract, iter_fields, refresh_status, value)
from app.models import ClinicalDocument, Doctor, FhirRecord, Patient, TermMapping, User, utcnow
from app.routers.fhir import store_record
from app.schemas import DocumentOut, DocumentSummary, DocumentUpdate, LoincAction, Page, ReasonRequest
from app.security import require
from app.services import audit, current_doctor, get_or_404, mark_approved, name_similarity, paginate, require_status

router = APIRouter(tags=["documents"])
log = logging.getLogger(__name__)

FILE_TYPES = {  # extension -> (content type, leading bytes)
    ".pdf": ("application/pdf", b"%PDF"), ".png": ("image/png", b"\x89PNG"), ".jpg": ("image/jpeg", b"\xff\xd8"),
    ".jpeg": ("image/jpeg", b"\xff\xd8"), ".gif": ("image/gif", b"GIF8"), ".webp": ("image/webp", b"RIFF"),
    ".docx": (DOCX_TYPE, b"PK"), ".txt": ("text/plain", b""),
}


# ---------- LOINC suggestions ----------

def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def loinc_suggestion(db: Session, hospital_id: int, test_name: str | None) -> LoincMapping:
    """Earlier confirmation for the same wording first, otherwise the mapping project's best match."""
    if not test_name:
        return LoincMapping()
    known = db.scalar(select(TermMapping).where(TermMapping.hospital_id == hospital_id,
                                                TermMapping.source_text == normalize(test_name)))
    if known:
        return LoincMapping(status="suggested", code=known.loinc_code, display=known.display,
                            matched_term="previously confirmed for this wording", confidence=1.0)
    try:
        match = loinc.suggest(test_name)
    except loinc.LoincUnavailable:
        return LoincMapping()
    return LoincMapping(status="suggested", **match) if match else LoincMapping()


@router.get("/terminology/loinc/suggest")
def suggest_loinc(q: str = Query(min_length=2), user: User = Depends(require("terminology:confirm")),
                  db: Session = Depends(get_db)):
    try:
        loinc.lookup("718-7")  # surfaces an unavailable dataset as a clear error
    except loinc.LoincUnavailable as exc:
        raise HTTPException(503, str(exc))
    return loinc_suggestion(db, user.hospital_id, q)


@router.get("/terminology/loinc/{code}")
def lookup_loinc(code: str, _: User = Depends(require("terminology:confirm"))):
    try:
        term = loinc.lookup(code)
    except loinc.LoincUnavailable as exc:
        raise HTTPException(503, str(exc))
    if term is None:
        raise HTTPException(404, f"{code} is not a LOINC code")
    return term


# ---------- extraction ----------

def run_extraction(document_id: int, user_id: int) -> None:
    """Background task: call the AI model and store the reviewable record (or a blank one on failure)."""
    with SessionLocal() as db:
        doc = db.get(ClinicalDocument, document_id)
        try:
            record = extract((settings.upload_dir / doc.storage_name).read_bytes(), doc.content_type, doc.file_name)
            for result in record.lab_results:
                result.loinc = loinc_suggestion(db, doc.hospital_id, value(result.test_name))
            doc.ai_output = doc.data = record.model_dump(mode="json")
            doc.document_type, doc.extraction_status, doc.extraction_error = record.document_type, "completed", None
            doc.ai_model = settings.ai_model
        except Exception as exc:  # any failure leaves the document usable for manual entry
            if not isinstance(exc, ExtractionError):
                log.exception("Unexpected extraction failure for document %s", document_id)
            doc.extraction_status, doc.extraction_error = "failed", str(exc)[:500]
            doc.data = doc.data or blank_record(doc.document_type).model_dump(mode="json")
        audit(db, db.get(User, user_id), "document.extract", "document", doc.id,
              {"status": doc.extraction_status, "model": settings.ai_model})
        db.commit()


# ---------- review state ----------

def requirements(doc: ClinicalDocument, record: ReviewedRecord | None) -> list[str]:
    """Everything that must be completed before the document can be submitted for approval."""
    needs = []
    if doc.patient_id is None:
        needs.append("Link the document to a registered patient")
    if doc.author_doctor_id is None:
        needs.append("Select the doctor responsible for this record")
    if record is None:
        return needs + ["Wait for extraction to finish"]
    uncertain = [p for p, f in iter_fields(record) if f.status == "uncertain" and f.review == "pending"]
    if uncertain:
        needs.append(f"Accept, edit or reject {len(uncertain)} low-confidence field(s)")
    for r in record.lab_results:
        if r.loinc.status == "suggested":
            needs.append(f"Confirm or reject the LOINC suggestion for '{value(r.test_name)}'")
    iso = r"\d{4}-\d{2}-\d{2}"
    kind, enc = doc.document_type, record.encounter
    if kind == "prescription" and not any(value(m.name) and (value(m.dosage) or value(m.frequency)) for m in record.medications):
        needs.append("Add at least one medicine with its dosage or frequency")
    if kind == "lab_report":
        if not any(value(r.test_name) and value(r.value) for r in record.lab_results):
            needs.append("Add at least one laboratory result with a value")
        if not value(record.report_conclusion):
            needs.append("Enter the report conclusion (required by the ABDM DiagnosticReport profile)")
    if kind == "opd_consultation" and not re.fullmatch(iso, value(enc.visit_date) or value(record.document_date) or ""):
        needs.append("Enter the visit date as YYYY-MM-DD")
    if kind == "discharge_summary" and not re.fullmatch(iso, value(enc.admission_date) or ""):
        needs.append("Enter the admission date as YYYY-MM-DD")
    if kind == "immunization" and not any(value(i.vaccine) and re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value(i.date) or "")
                                          for i in record.immunizations):
        needs.append("Add at least one vaccine with a date (YYYY, YYYY-MM or YYYY-MM-DD)")
    if kind == "wellness" and not any(value(v.name) and value(v.value) for v in record.vitals):
        needs.append("Add at least one measurement")
    return needs


def patient_warnings(doc: ClinicalDocument, record: ReviewedRecord | None) -> list[str]:
    """Differences between the document's patient details and the linked registered patient."""
    if not (record and doc.patient):
        return []
    warnings, p = [], record.patient
    if value(p.name) and name_similarity(value(p.name), doc.patient.full_name) < 0.85:
        warnings.append(f"Name on document '{value(p.name)}' differs from the registered patient '{doc.patient.full_name}'")
    mobile = re.sub(r"\D", "", value(p.mobile) or "")[-10:]
    if mobile and mobile != doc.patient.mobile:
        warnings.append(f"Mobile on document {mobile} differs from the registered {doc.patient.mobile}")
    gender = (value(p.gender) or "").strip().upper()[:1]
    if gender in ("M", "F") and gender != doc.patient.gender:
        warnings.append(f"Gender on document '{value(p.gender)}' differs from the registered patient")
    return warnings


def document_out(db: Session, doc: ClinicalDocument) -> dict:
    record = ReviewedRecord.model_validate(doc.data) if doc.data else None
    fields = [f for _, f in iter_fields(record)] if record else []
    counts = {
        "with_value": sum(f.value is not None and f.review != "rejected" for f in fields),
        "ai": sum(f.source == "ai" and f.value is not None for f in fields),
        "manual": sum(f.source == "manual" and f.value is not None for f in fields),
        "uncertain_pending": sum(f.status == "uncertain" and f.review == "pending" for f in fields),
        "rejected": sum(f.review == "rejected" for f in fields),
    }
    fhir_id = db.scalar(select(FhirRecord.id).where(FhirRecord.source_type == "document", FhirRecord.source_id == doc.id))
    out = DocumentOut.model_validate(doc).model_dump()
    return {**out, "requirements": requirements(doc, record), "review_counts": counts, "fhir_record_id": fhir_id,
            "warnings": patient_warnings(doc, record)}


def apply_provenance(db: Session, doc: ClinicalDocument, record: ReviewedRecord) -> ReviewedRecord:
    """Server-side truth for 'AI vs manual': a value that differs from the AI output is manual, and LOINC
    mappings can only change through the confirmation endpoint."""
    original = dict(iter_fields(ReviewedRecord.model_validate(doc.ai_output))) if doc.ai_output else {}
    for path, f in iter_fields(record):
        ai = original.get(path)
        if f.source == "ai" and (ai is None or ai.value != f.value):
            f.source, f.confidence, f.evidence = "manual", None, None
        if f.source == "manual" and f.value is not None and f.review != "rejected":
            f.review = "edited"
    previous = ReviewedRecord.model_validate(doc.data).lab_results if doc.data else []
    for i, r in enumerate(record.lab_results):
        before = previous[i] if i < len(previous) else None
        same_test = before is not None and value(before.test_name) == value(r.test_name)
        r.loinc = before.loinc if same_test else loinc_suggestion(db, doc.hospital_id, value(r.test_name))
    return refresh_status(record)


# ---------- endpoints ----------

@router.get("/documents", response_model=Page[DocumentSummary])
def list_documents(status: str | None = None, patient_id: int | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   user: User = Depends(require("document:read")), db: Session = Depends(get_db)):
    query = select(ClinicalDocument).where(ClinicalDocument.hospital_id == user.hospital_id) \
        .order_by(ClinicalDocument.id.desc())
    if status:
        query = query.where(ClinicalDocument.status.in_(status.split(",")))
    if patient_id:
        query = query.where(ClinicalDocument.patient_id == patient_id)
    return paginate(db, query, page, page_size)


@router.post("/documents", response_model=DocumentOut, status_code=201)
def upload_document(background: BackgroundTasks, request: Request, file: UploadFile = File(...),
                    patient_id: int | None = Form(None), user: User = Depends(require("document:write")),
                    db: Session = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in FILE_TYPES:
        raise HTTPException(415, "Supported files: PDF (including scanned), JPG, PNG, GIF, WEBP, DOCX and TXT")
    content_type, magic = FILE_TYPES[suffix]
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Files must be {settings.max_upload_mb} MB or smaller")
    if not data or not data.startswith(magic):
        raise HTTPException(415, f"The file content does not look like a {suffix} file")
    try:
        text = document_text(data, content_type)
    except Exception:
        raise HTTPException(415, "The DOCX file could not be read")
    if patient_id:
        get_or_404(db, Patient, patient_id, user.hospital_id)
    storage_name = f"{uuid4().hex}{suffix}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.upload_dir / storage_name).write_bytes(data)
    doc = ClinicalDocument(hospital_id=user.hospital_id, patient_id=patient_id, file_name=Path(file.filename).name[:255],
                           content_type=content_type, file_size=len(data), storage_name=storage_name, text=text,
                           created_by_id=user.id, status="draft")
    db.add(doc)
    db.flush()
    audit(db, user, "document.upload", "document", doc.id, {"type": suffix, "size": len(data)}, request)  # names can identify
    db.commit()
    background.add_task(run_extraction, doc.id, user.id)
    return document_out(db, doc)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, request: Request, user: User = Depends(require("document:read")),
                 db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    audit(db, user, "document.view", "document", doc.id, request=request)
    db.commit()
    return document_out(db, doc)


@router.get("/documents/{document_id}/file")
def document_file(document_id: int, user: User = Depends(require("document:read")), db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    return FileResponse(settings.upload_dir / doc.storage_name, media_type=doc.content_type, filename=doc.file_name,
                        content_disposition_type="inline")


@router.post("/documents/{document_id}/extract", response_model=DocumentOut)
def retry_extraction(document_id: int, background: BackgroundTasks, user: User = Depends(require("document:write")),
                     db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "draft", "rejected", action="extract again")
    if doc.ai_output and doc.data != doc.ai_output:
        raise HTTPException(409, "The record has already been reviewed; re-extracting would discard those changes")
    doc.extraction_status, doc.extraction_error, doc.data, doc.ai_output = "pending", None, None, None
    db.commit()
    background.add_task(run_extraction, doc.id, user.id)
    return document_out(db, doc)


@router.put("/documents/{document_id}", response_model=DocumentOut)
def save_review(document_id: int, body: DocumentUpdate, request: Request,
                user: User = Depends(require("document:write")), db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "draft", "rejected", action="edit document")
    changes = sorted(body.model_fields_set)
    if "patient_id" in body.model_fields_set:
        doc.patient_id = body.patient_id and get_or_404(db, Patient, body.patient_id, user.hospital_id).id
    if "author_doctor_id" in body.model_fields_set:
        doc.author_doctor_id = body.author_doctor_id and get_or_404(db, Doctor, body.author_doctor_id, user.hospital_id).id
    if body.document_type:
        doc.document_type = body.document_type
    if body.data is not None:
        record = apply_provenance(db, doc, ReviewedRecord.model_validate(body.data))
        record.document_type = doc.document_type
        doc.data = record.model_dump(mode="json")
    doc.status = "draft"
    audit(db, user, "document.review", "document", doc.id, {"changed": changes}, request)
    db.commit()
    db.refresh(doc)
    return document_out(db, doc)


@router.post("/documents/{document_id}/lab-results/{index}/loinc", response_model=DocumentOut)
def confirm_loinc(document_id: int, index: int, body: LoincAction, request: Request,
                  user: User = Depends(require("terminology:confirm")), db: Session = Depends(get_db)):
    """Confirm (optionally with a corrected code) or reject the LOINC mapping of one lab result."""
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "draft", "rejected", action="change LOINC mapping")
    record = ReviewedRecord.model_validate(doc.data)
    if not 0 <= index < len(record.lab_results):
        raise HTTPException(404, "Lab result not found")
    result, name = record.lab_results[index], value(record.lab_results[index].test_name)
    if body.action == "reject":
        result.loinc = LoincMapping(status="rejected", confirmed_by=user.full_name)
    else:
        code = (body.code or result.loinc.code or "").strip()
        try:
            term = loinc.lookup(code) if code else None
        except loinc.LoincUnavailable as exc:
            raise HTTPException(503, str(exc))
        if term is None:
            raise HTTPException(422, f"'{code}' is not a LOINC code")
        if term["status"] not in ("ACTIVE", "TRIAL"):
            raise HTTPException(422, f"LOINC {code} is {term['status'].lower()} and should not be used")
        result.loinc = LoincMapping(status="confirmed", code=code, display=term["display"],
                                    matched_term=result.loinc.matched_term if code == result.loinc.code else "entered manually",
                                    confidence=result.loinc.confidence if code == result.loinc.code else None,
                                    confirmed_by=user.full_name)
        if name:  # remembered as a suggestion for the same wording next time
            mapping = db.scalar(select(TermMapping).where(TermMapping.hospital_id == user.hospital_id,
                                                          TermMapping.source_text == normalize(name)))
            mapping = mapping or TermMapping(hospital_id=user.hospital_id, source_text=normalize(name))
            mapping.loinc_code, mapping.display, mapping.confirmed_by_id = code, term["display"], user.id
            mapping.confirmed_at = utcnow()
            db.add(mapping)
    doc.data = record.model_dump(mode="json")
    audit(db, user, f"loinc.{body.action}", "document", doc.id,
          {"test": name, "code": result.loinc.code, "index": index}, request)
    db.commit()
    return document_out(db, doc)


@router.post("/documents/{document_id}/submit", response_model=DocumentOut)
def submit_document(document_id: int, request: Request, user: User = Depends(require("document:write")),
                    db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "draft", "rejected", action="submit document")
    needs = requirements(doc, ReviewedRecord.model_validate(doc.data) if doc.data else None)
    if needs:
        raise HTTPException(422, {"message": "The document is not ready for approval", "requirements": needs})
    doc.status, doc.status_reason = "pending_approval", None
    audit(db, user, "document.submit", "document", doc.id, request=request)
    db.commit()
    return document_out(db, doc)


@router.post("/documents/{document_id}/approve", response_model=DocumentOut)
def approve_document(document_id: int, request: Request, user: User = Depends(require("document:approve")),
                     db: Session = Depends(get_db)):
    """A doctor approves the reviewed record; the FHIR Bundle is generated immediately."""
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "pending_approval", action="approve document")
    doctor = current_doctor(db, user)
    mark_approved(doc, user)
    doc.approved_by_doctor = doctor
    db.flush()
    audit(db, user, "document.approve", "document", doc.id, request=request)
    store_record(db, user, "document", doc.id, request)
    db.commit()
    return document_out(db, doc)


@router.post("/documents/{document_id}/reject", response_model=DocumentOut)
def reject_document(document_id: int, body: ReasonRequest, request: Request,
                    user: User = Depends(require("document:approve")), db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "pending_approval", action="reject document")
    current_doctor(db, user)
    doc.status, doc.status_reason = "rejected", body.reason
    audit(db, user, "document.reject", "document", doc.id, {"reason": body.reason}, request)
    db.commit()
    return document_out(db, doc)


@router.post("/documents/{document_id}/cancel", response_model=DocumentOut)
def cancel_document(document_id: int, body: ReasonRequest, request: Request,
                    user: User = Depends(require("document:write")), db: Session = Depends(get_db)):
    doc = get_or_404(db, ClinicalDocument, document_id, user.hospital_id)
    require_status(doc, "draft", "pending_approval", "rejected", action="cancel document")
    doc.status, doc.status_reason = "cancelled", body.reason
    audit(db, user, "document.cancel", "document", doc.id, {"reason": body.reason}, request)
    db.commit()
    return document_out(db, doc)

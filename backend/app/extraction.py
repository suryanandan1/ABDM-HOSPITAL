"""AI extraction of clinical documents (Mistral AI) into a reviewable, Pydantic-validated record.

Kept separate from FHIR generation: this module only turns a file into `ReviewedRecord` data.
PDFs (text or scanned) and images are read by Mistral OCR; DOCX and TXT text is extracted locally.
The text then goes to the Mistral chat model for structured extraction.
"""

import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from typing import Generic, Iterator, Literal, TypeVar

from pydantic import BaseModel, Field

from app import ai

DocumentType = Literal["prescription", "lab_report", "opd_consultation", "discharge_summary",
                       "immunization", "wellness", "clinical_note", "other"]
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED_TYPES = IMAGE_TYPES | {"application/pdf", "text/plain", DOCX_TYPE}
UNCERTAIN_BELOW = 0.8

F = TypeVar("F")


class AIField(BaseModel):
    value: str | None = Field(description="The value exactly as documented, or null if the document does not state it")
    confidence: float = Field(description="0 to 1: probability the value was read and assigned correctly")
    evidence: str | None = Field(description="Short verbatim quote from the document where the value appears")


class ReviewField(BaseModel):
    """A value plus where it came from and what staff decided about it."""
    value: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    source: Literal["ai", "manual"] = "manual"
    status: Literal["extracted", "uncertain", "missing"] = "missing"  # recomputed by refresh_status()
    review: Literal["pending", "accepted", "rejected", "edited"] = "pending"


class PatientDetails(BaseModel, Generic[F]):
    name: F
    age: F
    gender: F
    date_of_birth: F
    mobile: F
    abha_number: F
    hospital_patient_id: F


class DoctorDetails(BaseModel, Generic[F]):
    name: F
    registration_number: F
    qualification: F
    specialization: F
    facility_name: F


class EncounterDetails(BaseModel, Generic[F]):
    visit_date: F
    admission_date: F
    discharge_date: F
    chief_complaint: F
    history_of_present_illness: F
    examination_findings: F
    hospital_course: F
    treatment_plan: F
    follow_up: F
    instructions: F


class Diagnosis(BaseModel, Generic[F]):
    text: F
    icd10_code: F = Field(description="Only if an ICD-10 code is printed in the document")


class Allergy(BaseModel, Generic[F]):
    substance: F
    reaction: F


class Vital(BaseModel, Generic[F]):
    name: F
    value: F
    unit: F


class Medication(BaseModel, Generic[F]):
    name: F
    dosage: F
    route: F
    frequency: F
    duration: F
    quantity: F
    instructions: F


class LabResult(BaseModel, Generic[F]):
    test_name: F
    value: F
    unit: F
    reference_range: F
    flag: F = Field(description="Abnormal flag as printed (H, L, *), if any")
    panel_name: F


class Immunization(BaseModel, Generic[F]):
    vaccine: F
    date: F
    dose_number: F
    lot_number: F


class Extraction(BaseModel, Generic[F]):
    document_type: DocumentType
    document_date: F
    patient: PatientDetails[F]
    doctor: DoctorDetails[F]
    encounter: EncounterDetails[F]
    diagnoses: list[Diagnosis[F]]
    allergies: list[Allergy[F]]
    vitals: list[Vital[F]]
    medications: list[Medication[F]]
    lab_results: list[LabResult[F]]
    immunizations: list[Immunization[F]]
    report_conclusion: F
    notes: F


class LoincMapping(BaseModel):
    """LOINC suggestion for a lab result; only `confirmed` codes are used in FHIR."""
    status: Literal["none", "suggested", "confirmed", "rejected"] = "none"
    code: str | None = None
    display: str | None = None
    matched_term: str | None = None
    confidence: float | None = None
    confirmed_by: str | None = None


class ReviewedLabResult(LabResult[ReviewField]):
    loinc: LoincMapping = LoincMapping()


class ReviewedRecord(Extraction[ReviewField]):
    lab_results: list[ReviewedLabResult] = []


# ---------- review helpers ----------

def iter_fields(model: BaseModel, path: str = "") -> Iterator[tuple[str, ReviewField]]:
    """Yield (dotted path, field) for every ReviewField in a reviewed record."""
    for name in type(model).model_fields:
        value = getattr(model, name)
        items = value if isinstance(value, list) else [value]
        for i, item in enumerate(items):
            item_path = f"{path}{name}[{i}]" if isinstance(value, list) else f"{path}{name}"
            if isinstance(item, ReviewField):
                yield item_path, item
            elif isinstance(item, BaseModel) and not isinstance(item, LoincMapping):
                yield from iter_fields(item, item_path + ".")


def refresh_status(record: ReviewedRecord) -> ReviewedRecord:
    for _, f in iter_fields(record):
        if f.value is None or f.review == "rejected":
            f.status = "missing"
        elif f.source == "ai" and ((f.confidence or 0) < UNCERTAIN_BELOW or not f.evidence):
            f.status = "uncertain"
        else:
            f.status = "extracted"
    return record


def to_reviewed(extraction: Extraction[AIField]) -> ReviewedRecord:
    def convert(node):
        if isinstance(node, dict):
            if set(node) == {"value", "confidence", "evidence"}:
                return {**node, "source": "ai", "review": "pending"}
            return {k: convert(v) for k, v in node.items()}
        if isinstance(node, list):
            return [convert(v) for v in node]
        return node
    return refresh_status(ReviewedRecord.model_validate(convert(extraction.model_dump())))


def blank_record(document_type: str = "other") -> ReviewedRecord:
    """An empty record for manual entry (e.g. when AI extraction is unavailable)."""
    def build(cls, **extra):
        values = {}
        for name, f in cls.model_fields.items():
            if f.annotation is ReviewField:
                values[name] = ReviewField()
            elif getattr(f.annotation, "__origin__", None) is list:
                values[name] = []
            elif isinstance(f.annotation, type) and issubclass(f.annotation, BaseModel) and f.annotation is not LoincMapping:
                values[name] = build(f.annotation)
        return cls(**values, **extra)
    return build(ReviewedRecord, document_type=document_type)


def value(f: ReviewField | None) -> str | None:
    """The value to use downstream: rejected suggestions count as absent."""
    return None if f is None or f.review == "rejected" else f.value


# ---------- file handling and the model call ----------

class ExtractionError(Exception):
    pass


def docx_text(data: bytes) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return "\n".join("".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p")).strip()


def document_text(data: bytes, content_type: str) -> str | None:
    """Plain text for DOCX/TXT (used for the prompt and the preview); None for PDFs and images."""
    if content_type == DOCX_TYPE:
        return docx_text(data)
    if content_type == "text/plain":
        return data.decode("utf-8", errors="replace")
    return None


SYSTEM_PROMPT = """You extract structured data from one clinical document for a hospital records system.
Staff will review everything you return, so accuracy and honesty matter more than completeness.

Rules:
- Record only information explicitly present in the document. Never infer, complete, guess or convert values into codes.
  When the document does not state something, set value to null, confidence to 0 and evidence to null.
- Copy values as written, keeping units, strengths and abbreviations (e.g. "500 mg", "1-0-1", "BD", "after food").
- Write a date as YYYY-MM-DD only when day, month and year are all unambiguous; otherwise copy it as written.
- confidence is your probability that the value is read and assigned correctly; lower it for handwriting, blur,
  cut-off text or ambiguous layout.
- evidence is a short verbatim quote (at most about 20 words) showing where the value appears.
- Use one list item per diagnosis, allergy, vital sign, medicine, laboratory parameter and vaccine.
- Fill icd10_code only when an ICD-10 code is printed in the document.
- Choose document_type from the allowed values; use "other" when none fits."""


def content_blocks(data: bytes, content_type: str, filename: str) -> list[dict]:
    if content_type == "application/pdf" or content_type in IMAGE_TYPES:
        try:
            text = ai.ocr(data, content_type)
        except ai.AIError as exc:
            raise ExtractionError(str(exc)) from exc
    else:
        text = document_text(data, content_type)
    if not text:
        raise ExtractionError("The document contains no readable text")
    return [{"type": "text", "text": f"<document>\n{text}\n</document>"},
            {"type": "text", "text": f"Extract the clinical information from this document (file name: {filename})."}]


def call_model(blocks: list[dict]) -> Extraction[AIField]:
    """One structured-output request to Mistral. Replaced by a fake in tests."""
    try:
        return ai.structured(SYSTEM_PROMPT, blocks, Extraction[AIField])
    except ai.AIError as exc:
        raise ExtractionError(str(exc)) from exc


def extract(data: bytes, content_type: str, filename: str) -> ReviewedRecord:
    return to_reviewed(call_model(content_blocks(data, content_type, filename)))

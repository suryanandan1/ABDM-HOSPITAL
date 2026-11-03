"""Turn approved records into neutral FHIR engine input (app.fhir.ClinicalContent).

The only place that knows both the database models and the engine's input; app/fhir.py stays free of
business logic. Conversions are conservative: anything that cannot be read unambiguously (a date that is
not YYYY-MM-DD, an unconfirmed LOINC suggestion, an unknown route) stays as text and is reported as a
warning instead of being guessed.
"""

import base64
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache

from app import loinc
from app.config import settings
from app.extraction import ReviewedRecord, value
from app.fhir import (ICD10, VITAL_SIGNS, AllergyData, AttachmentData, ClinicalContent, Coding, ConditionData, EncounterData,
                      ImmunizationData, MedicationData, ObservationData, OrderData, ReportData, ResultData, LOINC)
from app.models import ClinicalDocument, Doctor, Encounter, LabOrder, Patient, Prescription, PrescriptionItem

RECORD_TYPES = {
    "prescription": "PrescriptionRecord", "lab_report": "DiagnosticReportRecord", "opd_consultation": "OPConsultRecord",
    "discharge_summary": "DischargeSummaryRecord", "immunization": "ImmunizationRecord", "wellness": "WellnessRecord",
    "clinical_note": "HealthDocumentRecord", "other": "HealthDocumentRecord",
}


@dataclass
class Source:
    content: ClinicalContent
    patient: Patient
    author: Doctor | None
    doctors: list[Doctor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def loinc_coding(code: str | None, warnings: list[str], what: str) -> Coding | None:
    if not code:
        return None
    try:
        term = loinc.lookup(code)
    except loinc.LoincUnavailable as exc:
        warnings.append(f"{what}: LOINC {code} not included because the LOINC dataset is unavailable ({exc})")
        return None
    if term is None:
        warnings.append(f"{what}: {code} is not a LOINC code, so it was left out")
        return None
    return Coding(system=LOINC, code=term["code"], display=term["display"])


@lru_cache(maxsize=2000)
def terminology_display(system: str, code: str) -> str | None:
    """Official display of a code from the terminology server (FHIR $lookup); None if unknown/unreachable."""
    if settings.fhir_tx_server == "n/a":
        return None
    url = f"{settings.fhir_tx_server}/r4/CodeSystem/$lookup?" + urllib.parse.urlencode({"system": system, "code": code})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/fhir+json"}),
                                    timeout=20) as response:
            parameters = json.load(response).get("parameter", [])
    except (OSError, ValueError):
        return None
    return next((p.get("valueString") for p in parameters if p.get("name") == "display"), None)


def icd10_coding(code: str | None, warnings: list[str], what: str) -> Coding | None:
    if not code:
        return None
    display = terminology_display(ICD10, code)
    if display is None:
        warnings.append(f"{what}: ICD-10 code {code} could not be verified on the terminology server, "
                        "so the diagnosis is exported as text only")
        return None
    return Coding(system=ICD10, code=code, display=display)


# ---------- Phase 2 records ----------

def phase2_medication(item: PrescriptionItem) -> MedicationData:
    return MedicationData(name=item.medicine_name, dosage=item.dosage, route=item.route, frequency=item.frequency,
                          duration_days=item.duration_days, quantity=item.quantity, instructions=item.instructions)


def consultation_source(e: Encounter, prescriptions: list[Prescription], orders: list[LabOrder]) -> Source:
    # Only the consulting doctor's prescriptions/orders belong to this note; others export on their own.
    prescriptions = [p for p in prescriptions if p.doctor_id == e.doctor_id]
    orders = [o for o in orders if o.doctor_id == e.doctor_id]
    warnings = []
    follow_up = " ".join(filter(None, [e.follow_up_instructions,
                                       f"Review on {e.follow_up_date.isoformat()}." if e.follow_up_date else None]))
    content = ClinicalContent(
        record_type="OPConsultRecord", key=f"consultation-{e.id}", title="Consultation record", date=e.approved_at,
        encounter=EncounterData(start=e.visit_at, reason=e.chief_complaint),
        conditions=[ConditionData(text=c.display, icd10=icd10_coding(c.code, warnings, c.display),
                                  clinical_status=c.clinical_status,
                                  verification=c.verification_status, onset=c.onset_date, note=c.note)
                    for c in e.conditions],
        observations=[ObservationData(name=VITAL_SIGNS[k][0], key=k, value=v, unit=VITAL_SIGNS[k][2])
                      for k, v in (e.vitals or {}).items() if v is not None and k in VITAL_SIGNS],
        medications=[phase2_medication(i) for p in prescriptions for i in p.items],
        orders=[OrderData(name=o.test.name, status="completed" if o.status == "completed" else "active",
                          priority=o.priority, authored=o.created_at, note=o.clinical_notes) for o in orders],
        chief_complaint=e.chief_complaint, history=e.history_present_illness, findings=e.clinical_findings,
        plan=e.treatment_plan, follow_up=follow_up or None)  # doctor_notes are internal and never exported
    return Source(content, e.patient, e.doctor, warnings=warnings)


def prescription_source(p: Prescription) -> Source:
    content = ClinicalContent(
        record_type="PrescriptionRecord", key=f"prescription-{p.id}", title="Prescription", date=p.approved_at,
        encounter=EncounterData(start=p.encounter.visit_at,
                                status="finished" if p.encounter.status == "approved" else "in-progress"),
        medications=[phase2_medication(i) for i in p.items], notes=p.notes)
    return Source(content, p.patient, p.doctor)


def lab_order_source(o: LabOrder) -> Source:
    r, warnings = o.report, []
    results = [ResultData(name=x.name, loinc=loinc_coding(x.loinc_code, warnings, x.name), value=x.value,
                          value_num=x.value_numeric, unit=x.unit, ref_low=x.ref_low, ref_high=x.ref_high,
                          ref_text=x.ref_text, interpretation=x.interpretation) for x in r.results]
    content = ClinicalContent(
        record_type="DiagnosticReportRecord", key=f"lab-order-{o.id}", title=f"Laboratory report: {o.test.name}",
        date=r.approved_at, encounter=EncounterData(start=o.created_at),
        orders=[OrderData(name=o.test.name, status="completed", priority=o.priority, authored=o.created_at,
                          note=o.clinical_notes, requester=o.doctor_id)],
        reports=[ReportData(name=o.test.name, loinc=loinc_coding(o.test.loinc_code, warnings, o.test.name),
                            results=results, conclusion=r.conclusion, issued=r.approved_at,
                            effective=o.sample_collected_at, interpreter=r.approved_by_doctor_id)])
    return Source(content, o.patient, r.approved_by_doctor, [o.doctor], warnings)


# ---------- reviewed documents ----------

def parse_iso_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text.strip()) if text else None
    except ValueError:
        return None


def parse_number(text: str | None) -> float | None:
    try:
        return float(text.replace(",", "").strip()) if text else None
    except ValueError:
        return None


def parse_range(text: str | None) -> tuple[float | None, float | None, str | None]:
    if not text:
        return None, None, None
    if m := re.fullmatch(r"\s*([\d.]+)\s*(?:-|–|to)\s*([\d.]+)\s*\S*\s*", text):
        return float(m[1]), float(m[2]), None
    if m := re.fullmatch(r"\s*(?:<|<=|≤|up to)\s*([\d.]+)\s*\S*\s*", text, re.I):
        return None, float(m[1]), None
    if m := re.fullmatch(r"\s*(?:>|>=|≥)\s*([\d.]+)\s*\S*\s*", text):
        return float(m[1]), None, None
    return None, None, text


FLAGS = {"H": "H", "HIGH": "H", "↑": "H", "L": "L", "LOW": "L", "↓": "L", "*": "A", "A": "A", "ABNORMAL": "A",
         "N": "N", "NORMAL": "N"}
ROUTE_WORDS = {"oral": "oral", "po": "oral", "by mouth": "oral", "iv": "iv", "intravenous": "iv", "im": "im",
               "intramuscular": "im", "sc": "sc", "s/c": "sc", "subcutaneous": "sc", "topical": "topical",
               "local application": "topical", "sublingual": "sublingual", "sl": "sublingual", "inhalation": "inhalation",
               "inhaled": "inhalation", "nasal": "nasal", "eye": "ophthalmic", "ophthalmic": "ophthalmic",
               "ear": "otic", "otic": "otic", "rectal": "rectal", "pr": "rectal"}
FREQUENCY_WORDS = {"od": "OD", "once daily": "OD", "once a day": "OD", "1-0-0": "OD", "0-0-1": "HS", "hs": "HS",
                   "at bedtime": "HS", "bd": "BD", "bid": "BD", "twice daily": "BD", "twice a day": "BD", "1-0-1": "BD",
                   "tds": "TDS", "tid": "TDS", "thrice daily": "TDS", "three times a day": "TDS", "1-1-1": "TDS",
                   "qid": "QID", "four times a day": "QID", "1-1-1-1": "QID", "q6h": "Q6H", "q8h": "Q8H",
                   "weekly": "WEEKLY", "once a week": "WEEKLY", "sos": "SOS", "prn": "SOS", "as needed": "SOS",
                   "stat": "STAT"}
VITAL_WORDS = {"temperature": "temperature", "temp": "temperature", "pulse": "pulse", "heart rate": "pulse",
               "hr": "pulse", "pr": "pulse", "respiratory rate": "respiratory_rate", "rr": "respiratory_rate",
               "spo2": "spo2", "oxygen saturation": "spo2", "sao2": "spo2", "weight": "weight", "wt": "weight",
               "height": "height", "ht": "height", "bp": "bp", "blood pressure": "bp"}
UNITS = {"°c": "Cel", "c": "Cel", "cel": "Cel", "°f": "[degF]", "f": "[degF]", "bpm": "/min", "/min": "/min",
         "per min": "/min", "beats/min": "/min", "breaths/min": "/min", "%": "%", "kg": "kg", "kgs": "kg",
         "lb": "[lb_av]", "lbs": "[lb_av]", "cm": "cm", "in": "[in_i]", "inch": "[in_i]", "m": "m",
         "mmhg": "mm[Hg]", "mm hg": "mm[Hg]"}
PER_MINUTE = {"pulse", "respiratory_rate"}  # always counted per minute, even when the unit is not written


def word(table: dict, text: str | None) -> str | None:
    return table.get(" ".join(text.lower().split())) if text else None


def document_vitals(record: ReviewedRecord) -> list[ObservationData]:
    result = []
    for v in record.vitals:
        name, raw, unit_text = value(v.name), value(v.value), value(v.unit)
        if not name or not raw:
            continue
        key = word(VITAL_WORDS, name)
        unit = word(UNITS, unit_text) or ("/min" if key in PER_MINUTE else "mm[Hg]" if key == "bp" else None)
        if key == "bp" and (m := re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", raw)) and unit == "mm[Hg]":
            result += [ObservationData(name="Systolic blood pressure", key="systolic_bp", value=float(m[1]), unit=unit),
                       ObservationData(name="Diastolic blood pressure", key="diastolic_bp", value=float(m[2]), unit=unit)]
        elif key and key != "bp" and (number := parse_number(raw)) is not None and unit:
            result.append(ObservationData(name=VITAL_SIGNS[key][0], key=key, value=number, unit=unit))
        else:  # kept as written when it cannot be coded safely
            result.append(ObservationData(name=name, value=parse_number(raw), value_text=raw, unit=unit_text))
    return result


def document_medication(m) -> MedicationData | None:
    name = value(m.name)
    if not name:
        return None
    duration = value(m.duration)
    days = None
    if duration and (d := re.fullmatch(r"\s*(?:x\s*)?(\d+)\s*(d|days?|w|wks?|weeks?)\s*", duration, re.I)):
        days = int(d[1]) * (7 if d[2].lower().startswith("w") else 1)
    frequency = value(m.frequency)
    quantity = parse_number(value(m.quantity))
    return MedicationData(name=name, dosage=value(m.dosage), route=word(ROUTE_WORDS, value(m.route)),
                          frequency=word(FREQUENCY_WORDS, frequency), frequency_text=frequency,
                          duration_days=days, duration_text=None if days else duration,
                          quantity=int(quantity) if quantity and quantity.is_integer() else None,
                          instructions=value(m.instructions))


def document_condition(d, warnings: list[str]) -> ConditionData:
    code = (value(d.icd10_code) or "").strip().upper() or None
    return ConditionData(text=value(d.text), icd10=icd10_coding(code, warnings, value(d.text)))


def document_source(doc: ClinicalDocument) -> Source:
    record, warnings = ReviewedRecord.model_validate(doc.data), []
    kind = RECORD_TYPES[doc.document_type]
    enc = record.encounter
    visit = parse_iso_date(value(enc.visit_date)) or parse_iso_date(value(record.document_date))
    admitted, discharged = parse_iso_date(value(enc.admission_date)), parse_iso_date(value(enc.discharge_date))
    for label, raw, parsed in (("Visit/document date", value(enc.visit_date) or value(record.document_date), visit),
                               ("Admission date", value(enc.admission_date), admitted),
                               ("Discharge date", value(enc.discharge_date), discharged)):
        if raw and parsed is None:
            warnings.append(f"{label} '{raw}' is not in YYYY-MM-DD format, so it was not used")

    encounter = None
    if kind == "DischargeSummaryRecord" and admitted:
        encounter = EncounterData(start=datetime.combine(admitted, datetime.min.time()), inpatient=True,
                                  end=datetime.combine(discharged, datetime.min.time()) if discharged else None)
    elif kind == "OPConsultRecord" and visit:
        encounter = EncounterData(start=datetime.combine(visit, datetime.min.time()), reason=value(enc.chief_complaint))

    results = []
    for r in record.lab_results:
        name, raw = value(r.test_name), value(r.value)
        if not name or raw is None:
            continue
        low, high, ref_text = parse_range(value(r.reference_range))
        code = r.loinc.code if r.loinc.status == "confirmed" else None
        if r.loinc.status == "suggested":
            warnings.append(f"{name}: LOINC suggestion {r.loinc.code} is not confirmed, so only the test name is used")
        results.append(ResultData(name=name, loinc=loinc_coding(code, warnings, name), value=raw,
                                  value_num=parse_number(raw), unit=value(r.unit), ref_low=low, ref_high=high,
                                  ref_text=ref_text, interpretation=FLAGS.get((value(r.flag) or "").strip().upper())))
    panels = {value(r.panel_name) for r in record.lab_results if value(r.panel_name)}
    reports = [ReportData(name=panels.pop() if len(panels) == 1 else "Laboratory report", results=results,
                          conclusion=value(record.report_conclusion), issued=doc.approved_at,
                          effective=datetime.combine(visit, datetime.min.time()) if visit else None,
                          interpreter=doc.approved_by_doctor_id)] if results else []

    immunizations = []
    for im in record.immunizations:
        if vaccine := value(im.vaccine):
            when, dose = value(im.date), parse_number(value(im.dose_number))
            partial = when if when and re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", when.strip()) else None
            if when and not partial:
                warnings.append(f"{vaccine}: date '{when}' must be YYYY, YYYY-MM or YYYY-MM-DD; correct it in review")
            immunizations.append(ImmunizationData(vaccine=vaccine, occurrence=partial and partial.strip(),
                                                  dose_number=int(dose) if dose and dose.is_integer() else None,
                                                  lot_number=value(im.lot_number)))

    data = (settings.upload_dir / doc.storage_name).read_bytes()
    content = ClinicalContent(
        record_type=kind, key=f"document-{doc.id}", title=doc.file_name, date=doc.approved_at, encounter=encounter,
        conditions=[document_condition(d, warnings) for d in record.diagnoses if value(d.text)],
        allergies=[AllergyData(text=s, reaction=value(a.reaction)) for a in record.allergies if (s := value(a.substance))],
        observations=document_vitals(record),
        medications=[m for m in map(document_medication, record.medications) if m],
        reports=reports, immunizations=immunizations,
        chief_complaint=value(enc.chief_complaint), history=value(enc.history_of_present_illness),
        findings=value(enc.examination_findings), plan=value(enc.treatment_plan), follow_up=value(enc.follow_up),
        hospital_course=value(enc.hospital_course),
        notes="\n".join(filter(None, [value(enc.instructions), value(record.notes)])) or None,
        attachment=AttachmentData(content_type=doc.content_type, data=base64.b64encode(data).decode(), title=doc.file_name))
    return Source(content, doc.patient, doc.author_doctor,
                  [doc.approved_by_doctor] if doc.approved_by_doctor else [], warnings)

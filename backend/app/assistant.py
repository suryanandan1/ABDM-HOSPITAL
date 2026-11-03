"""AI hospital assistant: a read-only view of one patient's records, limited to what the current user may see.

Timeline, doctor briefing and lab trends are computed without AI. Summaries and answers come from Mistral but
are checked: every statement must cite records from this user's corpus, dates must appear in those records,
and documented facts are separated from AI interpretation. Nothing here writes to clinical records.
"""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai
from app.extraction import ReviewedRecord, value
from app.hiu import records as hiu_records
from app.hiu.service import enforce_retention
from app.models import ClinicalDocument, Encounter, LabOrder, Patient, Prescription, RetrievedRecord, User
from app.security import permissions_for

MAX_ITEMS = 60  # most recent records sent to the AI


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def corpus(db: Session, user: User, patient: Patient) -> list[dict]:
    """Every record of the patient this user may read, newest first, as plain facts with a source id."""
    perms, items = permissions_for(user), []
    if "clinical:read" in perms:
        for e in db.scalars(select(Encounter).where(Encounter.patient_id == patient.id, Encounter.status == "approved")):
            items.append({"id": f"CONS-{e.id}", "type": "Consultation", "date": _iso(e.visit_at), "source": "This hospital",
                          "author": e.doctor.full_name, "link": f"/consultations/{e.id}", "facts": {
                              "chief_complaint": e.chief_complaint, "history": e.history_present_illness,
                              "findings": e.clinical_findings, "vitals": {k: v for k, v in (e.vitals or {}).items() if v is not None},
                              "diagnoses": [{"text": c.display, "icd10": c.code, "certainty": c.verification_status,
                                             "status": c.clinical_status} for c in e.conditions],
                              "plan": e.treatment_plan, "follow_up": e.follow_up_instructions}})
        for p in db.scalars(select(Prescription).where(Prescription.patient_id == patient.id, Prescription.status == "approved")):
            items.append({"id": f"RX-{p.id}", "type": "Prescription", "date": _iso(p.approved_at), "source": "This hospital",
                          "author": p.doctor.full_name, "link": f"/prescriptions/{p.id}", "facts": {
                              "medicines": [{"name": i.medicine_name, "dosage": i.dosage, "route": i.route, "frequency": i.frequency,
                                             "duration_days": i.duration_days} for i in p.items], "notes": p.notes}})
    if "lab:read" in perms:
        for o in db.scalars(select(LabOrder).where(LabOrder.patient_id == patient.id, LabOrder.status == "completed")):
            r = o.report
            items.append({"id": f"LAB-{o.id}", "type": "Laboratory report", "date": _iso(r.approved_at), "source": "This hospital",
                          "author": r.approved_by_name, "link": f"/lab/orders/{o.id}", "facts": {
                              "test": o.test.name, "conclusion": r.conclusion,
                              "results": [{"name": x.name, "loinc": x.loinc_code, "value": x.value, "value_numeric": x.value_numeric,
                                           "unit": x.unit, "reference_range": x.ref_text or (f"{x.ref_low}–{x.ref_high}"
                                                                                             if x.ref_low is not None or x.ref_high is not None else None),
                                           "flag": x.interpretation} for x in r.results]}})
    if "document:read" in perms:
        for d in db.scalars(select(ClinicalDocument).where(ClinicalDocument.patient_id == patient.id,
                                                           ClinicalDocument.status == "approved")):
            rec = ReviewedRecord.model_validate(d.data)
            items.append({"id": f"DOC-{d.id}", "type": f"Document: {d.document_type.replace('_', ' ')}",
                          "date": value(rec.encounter.visit_date) or value(rec.document_date) or _iso(d.approved_at),
                          "source": value(rec.doctor.facility_name) or "Uploaded document", "author": value(rec.doctor.name),
                          "link": f"/documents/{d.id}", "facts": {
                              "diagnoses": [value(x.text) for x in rec.diagnoses if value(x.text)],
                              "medicines": [{"name": value(m.name), "dosage": value(m.dosage), "frequency": value(m.frequency)}
                                            for m in rec.medications if value(m.name)],
                              "results": [{"name": value(r.test_name), "value": value(r.value), "unit": value(r.unit),
                                           "reference_range": value(r.reference_range), "flag": value(r.flag),
                                           "loinc": r.loinc.code if r.loinc.status == "confirmed" else None}
                                          for r in rec.lab_results if value(r.test_name)],
                              "chief_complaint": value(rec.encounter.chief_complaint),
                              "hospital_course": value(rec.encounter.hospital_course),
                              "conclusion": value(rec.report_conclusion), "notes": value(rec.notes)}})
    if "hiu:records" in perms:
        enforce_retention(db)  # expired or revoked external data is never shown
        for row in db.scalars(select(RetrievedRecord).where(RetrievedRecord.patient_id == patient.id)):
            facts = hiu_records.describe(hiu_records.bundle_json(row))
            items.append({"id": f"EXT-{row.id}", "type": f"External: {facts['category']}", "date": facts["date"],
                          "source": row.source_name or row.source_hip_id, "author": ", ".join(facts["authors"]) or None,
                          "link": "/hiu", "consent": row.consent_artefact_id, "facts": {
                              k: facts[k] for k in ("conditions", "medications", "observations", "reports", "sections",
                                                    "immunizations", "allergies") if facts[k]}})
    return sorted(items, key=lambda i: i["date"] or "", reverse=True)


def visible_categories(user: User) -> list[str]:
    perms = permissions_for(user)
    names = {"clinical:read": "consultations and prescriptions", "lab:read": "laboratory reports",
             "document:read": "approved uploaded documents", "hiu:records": "external records received under consent"}
    return [label for perm, label in names.items() if perm in perms]


# ---------- deterministic views ----------

def lab_observations(items: list[dict]) -> list[tuple[str, dict]]:
    """Lab results of internal, document and external records in the shape hiu.records.lab_trends expects."""
    out = []
    for item in items:
        facts = item["facts"]
        if item["id"].startswith("EXT-"):
            out.append((item["id"], {"observations": facts.get("observations", [])}))
            continue
        observations = [{"category": "laboratory", "name": r["name"], "loinc": r.get("loinc"),
                         "value": r.get("value_numeric") if r.get("value_numeric") is not None else _number(r.get("value")),
                         "value_text": r.get("value"), "unit": r.get("unit"), "date": item["date"],
                         "interpretation": r.get("flag"), "reference_range": r.get("reference_range")}
                        for r in facts.get("results", [])]
        if observations:
            out.append((item["id"], {"observations": observations}))
    return out


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def briefing(items: list[dict], patient: Patient, user: User) -> dict:
    """What a doctor sees on opening the patient: recent documented history, no AI, every line cited."""
    of = lambda prefix: [i for i in items if i["id"].startswith(prefix)]  # noqa: E731
    consultations, prescriptions, labs, external = of("CONS-"), of("RX-"), of("LAB-") + of("DOC-"), of("EXT-")
    active = [{"text": d["text"], "certainty": d["certainty"], "source_id": c["id"], "date": c["date"]}
              for c in consultations for d in c["facts"]["diagnoses"] if d["status"] in ("active", "recurrence")]
    lab_lines = [{"text": f"{r['name']}: {r['value']} {r.get('unit') or ''}".strip() + (f" ({r['flag']})" if r.get("flag") not in (None, "N") else ""),
                  "abnormal": r.get("flag") not in (None, "N"), "source_id": i["id"], "date": i["date"]}
                 for i in labs[:5] for r in i["facts"].get("results", [])]
    missing = []
    if not consultations:
        missing.append("No finalized consultations at this hospital")
    if not labs:
        missing.append("No approved laboratory reports")
    if not prescriptions:
        missing.append("No approved prescriptions")
    if not patient.abha_verified:
        missing.append("ABHA not verified: records from other providers cannot be requested yet")
    elif "hiu:records" in permissions_for(user) and not external:
        missing.append("No records from other providers have been retrieved")
    return {
        "active_conditions": active,
        "recent_consultations": [{"text": c["facts"]["chief_complaint"] or "Consultation",
                                  "diagnoses": [d["text"] for d in c["facts"]["diagnoses"]], "source_id": c["id"],
                                  "date": c["date"], "author": c["author"]} for c in consultations[:3]],
        "previous_prescriptions": [{"text": ", ".join(m["name"] for m in p["facts"]["medicines"]), "source_id": p["id"],
                                    "date": p["date"], "author": p["author"]} for p in prescriptions[:3]],
        "recent_lab_results": sorted(lab_lines, key=lambda line: not line["abnormal"])[:10],
        "external_records": [{"text": f"{e['type']} from {e['source']}", "source_id": e["id"], "date": e["date"]} for e in external[:5]],
        "missing_information": missing,
        "scope": visible_categories(user),
    }


# ---------- AI summary and questions ----------

class Statement(BaseModel):
    text: str
    kind: Literal["documented_fact", "ai_interpretation"]
    source_ids: list[str] = Field(description="Ids of the records the statement is based on")
    dates: list[str] = Field(description="Dates (YYYY-MM-DD) taken from the cited records")


class Summary(BaseModel):
    consultations: list[Statement]
    conditions: list[Statement]
    medications: list[Statement]
    laboratory: list[Statement]
    observations: list[Statement]
    external_records: list[Statement]
    missing_information: list[str]


class Answer(BaseModel):
    statements: list[Statement]
    information_unavailable: bool
    unavailable_reason: str | None


RULES = """You assist doctors and authorised hospital staff by reading a patient's records, provided as JSON items with
an "id". Rules you must follow:
- Use only the provided records. Never invent findings, tests, results, medicines, dates or diagnoses.
- Never state that a test was done or a medicine was given unless a record documents it.
- Do not diagnose, do not recommend treatment and do not approve or change prescriptions. You may point out
  documented patterns (e.g. a result changing between two dated reports); label those as "ai_interpretation".
- A statement copied or directly restated from records is "documented_fact"; anything combining, comparing or
  judging is "ai_interpretation". Every statement lists the ids it relies on and the record dates (YYYY-MM-DD).
- Diagnoses recorded as provisional or differential must be described as such, never as confirmed.
- If the records do not contain the answer, say it is not available in the records you were given."""

SUMMARY_TASK = "Summarise the patient's documented history in the sections provided and list missing information."


def _check(statements: list[dict], by_id: dict[str, dict]) -> tuple[list[dict], int]:
    """Keep statements that cite known records; keep only dates that occur in the cited records."""
    kept = []
    for s in statements:
        if not s["source_ids"] or not set(s["source_ids"]) <= set(by_id):
            continue
        known_dates = {(by_id[i]["date"] or "")[:10] for i in s["source_ids"]}
        s["dates"] = [d for d in s["dates"] if d[:10] in known_dates]
        kept.append(s)
    return kept, len(statements) - len(kept)


def _payload(items: list[dict], extra: dict) -> list[dict]:
    included = items[:MAX_ITEMS]
    records = [{k: i[k] for k in ("id", "type", "date", "source", "author", "facts")} for i in included]
    return [{"type": "text", "text": json.dumps({"records": records, **extra}, default=str)}]


def call_model(system: str, content: list[dict], output_format):
    """The model call; replaced by a fake in tests."""
    return ai.structured(system, content, output_format, max_tokens=8000)


def summarize(items: list[dict]) -> dict:
    by_id = {i["id"]: i for i in items[:MAX_ITEMS]}
    summary = call_model(RULES, _payload(items, {"task": SUMMARY_TASK}), Summary).model_dump()
    removed = 0
    for section in ("consultations", "conditions", "medications", "laboratory", "observations", "external_records"):
        summary[section], dropped = _check(summary[section], by_id)
        removed += dropped
    return {**summary, "removed_unsupported": removed, "records_considered": len(by_id),
            "records_omitted": max(len(items) - MAX_ITEMS, 0)}


def ask(items: list[dict], question: str) -> dict:
    by_id = {i["id"]: i for i in items[:MAX_ITEMS]}
    answer = call_model(RULES, _payload(items, {"question": question}), Answer).model_dump()
    answer["statements"], removed = _check(answer["statements"], by_id)
    if not answer["statements"] and not answer["information_unavailable"]:
        answer.update(information_unavailable=True,
                      unavailable_reason="No answer could be traced to the patient's records")
    return {**answer, "removed_unsupported": removed, "records_considered": len(by_id)}

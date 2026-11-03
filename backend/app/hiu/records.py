"""Retrieved FHIR records: sealed storage, a read-only normalised view, timeline and lab trends.

The original decrypted JSON is stored exactly as received (sealed at rest) and never modified; everything
here is derived from it on demand.
"""

import base64
import json
import os
import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

NRCES = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"
CATEGORIES = {"OPConsultRecord": "OPD consultation", "PrescriptionRecord": "Prescription",
              "DiagnosticReportRecord": "Laboratory report", "DischargeSummaryRecord": "Discharge summary",
              "ImmunizationRecord": "Immunization", "WellnessRecord": "Wellness record",
              "HealthDocumentRecord": "Health document"}
SNOMED_TYPES = {"371530004": "OPConsultRecord", "440545006": "PrescriptionRecord", "721981007": "DiagnosticReportRecord",
                "373942005": "DischargeSummaryRecord", "41000179103": "ImmunizationRecord", "419891008": "HealthDocumentRecord"}


# ---------- sealing (encryption at rest) ----------

def _key() -> AESGCM:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"abdm-hospital-hiu-storage", info=b"v1").derive(
        settings.jwt_secret.encode())
    return AESGCM(key)


def seal(text: str) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + _key().encrypt(nonce, text.encode(), None)).decode()


def unseal(sealed: str) -> str:
    raw = base64.b64decode(sealed)
    return _key().decrypt(raw[:12], raw[12:], None).decode()


# ---------- normalised, read-only view ----------

def _text(concept: dict | None) -> str | None:
    if not concept:
        return None
    return concept.get("text") or next((c.get("display") for c in concept.get("coding", []) if c.get("display")), None)


def _code(concept: dict | None, system: str) -> str | None:
    return next((c.get("code") for c in (concept or {}).get("coding", []) if c.get("system") == system), None)


def _narrative(resource: dict) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", (resource.get("text") or {}).get("div", "")).split())


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00") if len(value) > 10 else value + "T00:00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)


def record_type(bundle: dict) -> str | None:
    composition = next((e["resource"] for e in bundle.get("entry", []) if e.get("resource", {}).get("resourceType") == "Composition"), {})
    for profile in (composition.get("meta") or {}).get("profile", []):
        if profile.startswith(NRCES) and profile.removeprefix(NRCES) in CATEGORIES:
            return profile.removeprefix(NRCES)
    code = _code(composition.get("type"), "http://snomed.info/sct")
    if code in SNOMED_TYPES:
        return SNOMED_TYPES[code]
    return "WellnessRecord" if (composition.get("type") or {}).get("text") == "Wellness Record" else None


def describe(bundle: dict) -> dict:
    """Facts shown to doctors and given to the AI: copied from the bundle, never inferred."""
    resources = [e.get("resource", {}) for e in bundle.get("entry", [])]
    by_type = lambda t: [r for r in resources if r.get("resourceType") == t]  # noqa: E731
    composition = (by_type("Composition") or [{}])[0]
    kind = record_type(bundle)
    when = composition.get("date")
    observations = []
    for o in by_type("Observation"):
        quantity = o.get("valueQuantity") or {}
        ref = (o.get("referenceRange") or [{}])[0]
        category = _code((o.get("category") or [{}])[0], "http://terminology.hl7.org/CodeSystem/observation-category")
        observations.append({
            "name": _text(o.get("code")), "loinc": _code(o.get("code"), "http://loinc.org"), "category": category,
            "value": quantity.get("value"), "unit": quantity.get("unit") or quantity.get("code"),
            "value_text": o.get("valueString"), "date": o.get("effectiveDateTime") or when,
            "reference_range": ref.get("text") or " – ".join(str(ref[k]["value"]) for k in ("low", "high") if k in ref) or None,
            "interpretation": _code((o.get("interpretation") or [{}])[0], "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
            "components": [{"name": _text(c.get("code")), "value": (c.get("valueQuantity") or {}).get("value"),
                            "unit": (c.get("valueQuantity") or {}).get("unit")} for c in o.get("component", [])] or None,
        })
    return {
        "record_type": kind, "category": CATEGORIES.get(kind, "Other record"), "date": when,
        "title": composition.get("title"),
        "source": next((o.get("name") for o in by_type("Organization")), None),
        "authors": [a.get("display") for a in composition.get("author", []) if a.get("display")],
        "conditions": [{"text": _text(c.get("code")), "icd10": _code(c.get("code"), "http://hl7.org/fhir/sid/icd-10"),
                        "status": _code(c.get("clinicalStatus"), "http://terminology.hl7.org/CodeSystem/condition-clinical")}
                       for c in by_type("Condition")],
        "medications": [{"name": _text(m.get("medicationCodeableConcept")),
                         "dosage": ((m.get("dosageInstruction") or [{}])[0]).get("text"), "status": m.get("status")}
                        for m in by_type("MedicationRequest")],
        "observations": observations,
        "reports": [{"name": _text(d.get("code")), "conclusion": d.get("conclusion")} for d in by_type("DiagnosticReport")],
        "immunizations": [{"vaccine": _text(i.get("vaccineCode")), "date": i.get("occurrenceDateTime")} for i in by_type("Immunization")],
        "allergies": [_text(a.get("code")) for a in by_type("AllergyIntolerance")],
        "sections": [{"title": s.get("title"), "text": _narrative(s)} for s in composition.get("section", []) if _narrative(s)],
        "attachments": [d.get("content", [{}])[0].get("attachment", {}).get("title") for d in by_type("DocumentReference")],
    }


def timeline(records: list[tuple[int, dict]]) -> list[dict]:
    """[(record id, describe() facts)] -> chronological entries, newest first."""
    entries = [{"record_id": rid, "date": f["date"], "category": f["category"], "source": f["source"],
                "highlights": [c["text"] for c in f["conditions"] if c["text"]][:3]
                + [m["name"] for m in f["medications"] if m["name"]][:3]
                + [r["name"] for r in f["reports"] if r["name"]][:2]} for rid, f in records]
    return sorted(entries, key=lambda e: _time(e["date"]) or datetime.min, reverse=True)


def lab_trends(records: list[tuple[int, dict]]) -> list[dict]:
    """Laboratory tests seen more than once. Values are compared only for the same test (LOINC code, else the
    exact name) in the same unit; otherwise the repeat is listed without comparison."""
    groups: dict[tuple, dict] = {}
    for rid, f in records:
        for o in f["observations"]:
            if o["category"] != "laboratory" or not o["name"]:
                continue
            key = ("loinc", o["loinc"]) if o["loinc"] else ("name", o["name"].strip().lower())
            group = groups.setdefault(key, {"test": o["name"], "loinc": o["loinc"], "points": []})
            group["points"].append({"record_id": rid, "date": o["date"], "value": o["value"], "value_text": o["value_text"],
                                    "unit": o["unit"], "interpretation": o["interpretation"],
                                    "reference_range": o["reference_range"]})
    trends = []
    for group in groups.values():
        if len(group["points"]) < 2:
            continue
        points = sorted(group["points"], key=lambda p: _time(p["date"]) or datetime.min)
        units = {p["unit"] for p in points}
        comparable = len(units) == 1 and all(isinstance(p["value"], (int, float)) for p in points)
        trend = {**group, "points": points, "comparable": comparable,
                 "note": None if comparable else "Not compared: units differ or values are not numeric"}
        if comparable:
            first, last = points[0]["value"], points[-1]["value"]
            trend["change"] = round(last - first, 3)
        trends.append(trend)
    return sorted(trends, key=lambda t: t["test"].lower())


def bundle_json(record) -> dict:
    return json.loads(unseal(record.content))

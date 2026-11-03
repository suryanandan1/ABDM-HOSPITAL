"""FHIR validation, in two clearly separated layers.

1. prechecks(): fast local checks of Bundle structure, references and data the ABDM profiles require.
   Useful feedback, NOT profile validation.
2. validate(): the official HL7 FHIR Validator (Java) with FHIR 4.0.1 and the ABDM IG package, including
   terminology checks against FHIR_TX_SERVER. Only this result decides whether a record is valid.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from app.config import settings
from app.fhir import FHIR_VERSION, IG_PACKAGE, NRCES


def issue(severity: str, category: str, message: str, location: str | None = None) -> dict:
    return {"severity": severity, "category": category, "message": message, "location": location}


def describe(entry: dict) -> str:
    """Readable name of a bundle entry, e.g. 'Observation (Haemoglobin)'."""
    r = entry.get("resource", {})
    concept = r.get("code") or r.get("medicationCodeableConcept") or r.get("vaccineCode") or {}
    label = concept.get("text") or r.get("title") or r.get("name")
    if isinstance(label, list):
        label = label[0].get("text") if label else None
    return f"{r.get('resourceType', '?')} ({label})" if isinstance(label, str) else r.get("resourceType", "?")


def find_references(node, path: str = ""):
    if isinstance(node, dict):
        for key, child in node.items():
            if key == "reference" and isinstance(child, str):
                yield path, child
            else:
                yield from find_references(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from find_references(child, f"{path}[{i}]")


# Elements the ABDM profiles require (min=1) that depend on source data rather than on the engine.
REQUIRED = {
    "DiagnosticReport": [("result", "at least one result"), ("conclusion", "a conclusion"),
                         ("resultsInterpreter", "an approving doctor (results interpreter)")],
    "MedicationRequest": [("dosageInstruction", "dosage instructions"), ("requester", "a prescriber")],
    "Immunization": [("occurrenceDateTime", "a vaccination date as YYYY, YYYY-MM or YYYY-MM-DD")],
    "Composition": [("section", "at least one section with content"), ("author", "an author")],
}


def prechecks(bundle: dict, warnings: list[str] = ()) -> list[dict]:
    issues = [issue("warning", "data", w) for w in warnings]
    entries = bundle.get("entry", [])
    if bundle.get("type") != "document":
        issues.append(issue("error", "structure", "Bundle type must be 'document'"))
    if not entries or entries[0]["resource"].get("resourceType") != "Composition":
        issues.append(issue("error", "structure", "The first entry of a document Bundle must be the Composition"))
        return issues
    urls = [e.get("fullUrl") for e in entries]
    if len(urls) != len(set(urls)):
        issues.append(issue("error", "structure", "Two entries share the same fullUrl"))
    known = set(urls)
    for i, entry in enumerate(entries):
        r = entry["resource"]
        for path, target in find_references(r):
            if target.startswith("urn:uuid:") and target not in known:
                issues.append(issue("error", "reference", f"{describe(entry)} refers to {target}, which is not in the bundle",
                                    f"entry[{i}].{path}"))
        for fields, what in REQUIRED.get(r["resourceType"], []):
            if not any(r.get(f) for f in fields.split("|")):
                issues.append(issue("error", "required", f"{describe(entry)} needs {what}", f"entry[{i}]"))
    composition = entries[0]["resource"]
    record_type = composition["meta"]["profile"][0].removeprefix(NRCES)
    if record_type in ("OPConsultRecord", "DischargeSummaryRecord") and not composition.get("encounter"):
        what = "an admission date" if record_type == "DischargeSummaryRecord" else "a visit date"
        issues.append(issue("error", "required", f"{record_type} needs an encounter, which needs {what} (YYYY-MM-DD)"))
    return issues


# ---------- official HL7 validator ----------

def java_executable() -> str:
    if settings.java_path:
        return settings.java_path
    tools = settings.fhir_validator_jar.parent
    bundled = sorted(tools.glob("jdk-*/bin/java.exe")) + sorted(tools.glob("jdk-*/bin/java"))
    return str(bundled[0]) if bundled else "java"


def validator_available() -> bool:
    return settings.fhir_validator_jar.exists()


def categorise(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("minimum required", "is required", "cardinality")):
        return "required"
    if any(k in lowered for k in ("code", "valueset", "value set", "display", "codesystem", "terminology")):
        return "terminology"
    if any(k in lowered for k in ("reference", "resolve", "not found in the bundle", "reachable")):
        return "reference"
    if any(k in lowered for k in ("slice", "profile", "fixed value", "pattern")):
        return "profile"
    return "other"


def plain_location(expression: str | None, entries: list[dict]) -> str | None:
    """'Bundle.entry[3].resource.code' -> 'Observation (Haemoglobin) › code'."""
    if not expression:
        return None
    m = re.match(r"Bundle\.entry\[(\d+)\]\.resource(?:/\*[^*]*\*/)?\.?(.*)", expression)
    if not m or int(m[1]) >= len(entries):
        return expression
    element = m[2].replace(".", " › ")
    return f"{describe(entries[int(m[1])])}{' › ' + element if element else ''}"


def validate(bundle: dict) -> tuple[str, list[dict], str]:
    """Run the HL7 validator. Returns (status, issues, validator description)."""
    if not validator_available():
        return "error", [issue("error", "validator",
                               "HL7 FHIR validator is not installed. Run scripts\\setup_validator.ps1 in the backend folder.")], ""
    with tempfile.TemporaryDirectory() as tmp:
        source, output = Path(tmp) / "bundle.json", Path(tmp) / "outcome.json"
        source.write_text(json.dumps(bundle), encoding="utf-8")
        command = [java_executable(), "-jar", str(settings.fhir_validator_jar), str(source), "-version", FHIR_VERSION,
                   "-ig", IG_PACKAGE, "-tx", settings.fhir_tx_server, "-output", str(output)]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "error", [issue("error", "validator", f"The validator could not run: {exc}")], ""
        version = next((line.strip() for line in run.stdout.splitlines() if "FHIR Validation tool" in line), "HL7 FHIR Validator")
        if not output.exists():
            return "error", [issue("error", "validator", "The validator produced no result: " + run.stdout[-500:])], version
        outcome = json.loads(output.read_text(encoding="utf-8"))
    entries = bundle.get("entry", [])
    issues = []
    for i in outcome.get("issue", []):
        text = (i.get("details") or {}).get("text") or i.get("diagnostics") or ""
        expression = (i.get("expression") or i.get("location") or [None])[0]
        severity = "error" if i["severity"] in ("error", "fatal") else i["severity"]
        issues.append({**issue(severity, categorise(text), text, plain_location(expression, entries)),
                       "expression": expression})
    status = "invalid" if any(x["severity"] == "error" for x in issues) else "valid"
    description = f"{version} · FHIR {FHIR_VERSION} · {IG_PACKAGE} · terminology: {settings.fhir_tx_server}"
    return status, issues, description

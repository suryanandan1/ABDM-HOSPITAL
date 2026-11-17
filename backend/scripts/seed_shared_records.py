"""Give the demo network (python init_db.py --demo) records to exchange through ABDM, so the HIP/HIU workflow
between its hospitals can be seen end to end. Simulation mode only.

For each visit below, the hospital's own staff work through the normal API exactly as in the app: the doctor
records and finalizes a consultation and approves a prescription, the lab enters results that another doctor
approves, and health information staff generate the FHIR documents. The documents are checked with the official
HL7 FHIR validator (about a minute each; several run in parallel), and only valid ones become care contexts.
Finally each hospital verifies every demo patient's ABHA address with (simulated) ABDM and links the care
contexts to it, which is what makes them discoverable by the other hospitals.

After it, sign in at another hospital (e.g. admin at City General Hospital), open External records (ABDM HIU),
request records for Surya Prakash (surya@abdm) and use "Simulate: patient grants": the records arrive from
Green Valley and Riverside, each hospital's ABDM HIP page shows the consent and the transfer.

Usage (from backend/, with the venv active, after python init_db.py --demo):
    python scripts/seed_shared_records.py              # safe to run again: completed visits are skipped
    python scripts/seed_shared_records.py --workers 2  # fewer validator processes in parallel (less memory)
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, select  # noqa: E402

from app import demo, fhir_validation  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Encounter, FhirRecord, Hospital, Patient, User, utcnow  # noqa: E402
from app.services import audit  # noqa: E402
from init_db import DEMO_PASSWORD  # noqa: E402

# Usernames per hospital; the primary hospital uses init_db.seed_demo's accounts.
TEAM = {
    demo.PRIMARY: {"hims": "hims", "labtech": "labtech"},
    "GVH": {"hims": "gvh.hims", "labtech": "gvh.labtech"},
    "RMC": {"hims": "rmc.hims", "labtech": "rmc.labtech"},
}
AMLODIPINE = {"medicine_name": "Amlodipine 5 mg tablet", "dosage": "1 tablet", "route": "oral", "frequency": "OD",
              "duration_days": 30, "quantity": 30, "instructions": "In the morning"}
METFORMIN = {"medicine_name": "Metformin 500 mg tablet", "dosage": "1 tablet", "route": "oral", "frequency": "BD",
             "duration_days": 90, "quantity": 180, "instructions": "After meals"}
PARACETAMOL = {"medicine_name": "Paracetamol 650 mg tablet", "dosage": "1 tablet", "route": "oral", "frequency": "SOS",
               "duration_days": 5, "quantity": 10, "instructions": "For fever above 38 C"}
HYPERTENSION = {"display": "Essential (primary) hypertension", "code": "I10", "verification_status": "confirmed"}
DIABETES = {"display": "Type 2 diabetes mellitus", "code": "E11.9", "verification_status": "confirmed"}
VIRAL = {"display": "Viral infection, unspecified", "code": "B34.9", "verification_status": "provisional"}

# (hospital, ABHA address, doctor, lab approver, consultation, medicines, (test code, results, conclusion))
VISITS = [
    ("GVH", "surya@abdm", "gvh.shetty", "gvh.kulkarni",
     {"chief_complaint": "Chest discomfort on climbing stairs", "vitals": {"pulse": 88, "systolic_bp": 148, "diastolic_bp": 94},
      "diagnoses": [HYPERTENSION], "treatment_plan": "Start amlodipine; low-salt diet; review with lipid profile"},
     [AMLODIPINE], ("LIPID", {"CHOL": "236", "TG": "182", "HDL": "38", "LDL": "158"}, "Raised total and LDL cholesterol; low HDL")),
    ("GVH", "ramesh.kumar@abdm", "gvh.kulkarni", "gvh.shetty",
     {"chief_complaint": "Follow-up of type 2 diabetes", "vitals": {"pulse": 76, "systolic_bp": 128, "diastolic_bp": 82},
      "diagnoses": [DIABETES], "treatment_plan": "Continue metformin; repeat fasting glucose in 3 months"},
     [METFORMIN], ("FBS", {"GLU": "142"}, "Fasting glucose above target")),
    ("RMC", "surya@abdm", "rmc.sheikh", "rmc.malhotra",
     {"chief_complaint": "Cough and fever for five days",
      "vitals": {"temperature": 38.4, "pulse": 96, "respiratory_rate": 20, "spo2": 97},
      "diagnoses": [VIRAL], "treatment_plan": "Rest, fluids, paracetamol as needed; return if breathless"},
     [PARACETAMOL], ("CBC", {"HB": "13.8", "WBC": "11.6", "PLT": "210"}, "Mild leucocytosis")),
    ("RMC", "sita.devi@abdm", "rmc.malhotra", "rmc.sheikh",
     {"chief_complaint": "Headache and high blood pressure at a pharmacy check",
      "vitals": {"pulse": 82, "systolic_bp": 152, "diastolic_bp": 96}, "diagnoses": [HYPERTENSION],
      "treatment_plan": "Start amlodipine; home blood pressure log"},
     [AMLODIPINE], ("CBC", {"HB": "11.2", "WBC": "6.4", "PLT": "290"}, "Haemoglobin slightly low")),
    (demo.PRIMARY, "ramesh.kumar@abdm", "dr.mehta", "dr.rao",
     {"chief_complaint": "Increased thirst and frequent urination", "vitals": {"pulse": 80, "systolic_bp": 134, "diastolic_bp": 86},
      "diagnoses": [DIABETES], "treatment_plan": "Start metformin; diet advice"},
     [METFORMIN], ("FBS", {"GLU": "168"}, "Raised fasting glucose")),
]


class Api:
    """The app's own API, in process, signed in as the hospital's staff."""

    def __init__(self):
        self.client, self.tokens = TestClient(app), {}

    def can_sign_in(self, username: str) -> bool:
        """True if the demo password still works (not changed, no two-step sign-in) for this account."""
        if username not in self.tokens:
            r = self.client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
            self.client.cookies.clear()
            token = r.json().get("access_token") if r.status_code == 200 else None
            if not token:
                return False
            self.tokens[username] = {"Authorization": f"Bearer {token}"}
        return True

    def headers(self, username: str) -> dict:
        if not self.can_sign_in(username):
            sys.exit(f"Cannot sign in as {username} with the demo password: run python init_db.py --demo first "
                     "(an account whose password was changed or that uses two-step sign-in cannot be used)")
        return self.tokens[username]

    def __call__(self, method: str, path: str, username: str, body: dict | None = None, ok=(200, 201)):
        r = self.client.request(method, f"/api{path}", headers=self.headers(username), json=body)
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {path} as {username}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()


def hospital_by_code(db, code: str) -> Hospital:
    if code == demo.PRIMARY:
        return db.scalar(select(Hospital).where(Hospital.code == settings.hospital_code.upper()))
    return db.scalar(select(Hospital).where(Hospital.code == code))


def patient_at(db, hospital: Hospital, address: str) -> Patient | None:
    return db.scalar(select(Patient).where(Patient.hospital_id == hospital.id, Patient.abha_address == address))


def record_visit(api: Api, code: str, patient: Patient, doctor: str, approver: str, consultation: dict,
                 medicines: list[dict], lab: tuple) -> list[tuple[str, int]]:
    """One outpatient visit through the API. Returns the FHIR sources created: [(source type, id)]."""
    labtech = TEAM[code]["labtech"]
    visit = api("POST", "/consultations", doctor, {"patient_id": patient.id})
    api("PUT", f"/consultations/{visit['id']}", doctor, consultation)
    rx = api("POST", "/prescriptions", doctor, {"encounter_id": visit["id"], "items": medicines})
    api("POST", f"/prescriptions/{rx['id']}/submit", doctor)
    api("POST", f"/prescriptions/{rx['id']}/approve", doctor)
    test_code, results, conclusion = lab
    test = next(t for t in api("GET", "/lab/tests", labtech) if t["code"] == test_code)
    order = api("POST", "/lab/orders", doctor, {"encounter_id": visit["id"], "test_ids": [test["id"]]})[0]
    api("POST", f"/consultations/{visit['id']}/finalize", doctor)
    api("POST", f"/lab/orders/{order['id']}/sample", labtech, {})
    api("PUT", f"/lab/orders/{order['id']}/report", labtech,
        {"results": [{"code": k, "value": v} for k, v in results.items()], "conclusion": conclusion})
    api("POST", f"/lab/orders/{order['id']}/report/submit", labtech)
    api("POST", f"/lab/orders/{order['id']}/report/approve", approver)
    return [("consultation", visit["id"]), ("prescription", rx["id"]), ("lab_order", order["id"])]


def validate_all(record_ids: list[int], workers: int) -> None:
    """HL7-validate the FHIR records (the same check as the Validate button), several validator processes at once."""
    with SessionLocal() as db:
        bundles = {r.id: r.bundle for r in db.scalars(select(FhirRecord).where(FhirRecord.id.in_(record_ids)))}
    print(f"Validating {len(bundles)} FHIR document(s) with the HL7 validator ({workers} at a time; about a minute each)...")

    def run(record_id: int):
        return record_id, fhir_validation.validate(bundles[record_id])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record_id, (status, issues, validator) in pool.map(run, bundles):
            with SessionLocal() as db:
                record = db.get(FhirRecord, record_id)
                staff = db.scalar(select(User).where(User.username == TEAM_BY_HOSPITAL_ID[record.hospital_id]["hims"]))
                record.validation_status, record.validation_issues = status, issues
                record.validator, record.validated_at = validator, utcnow()
                audit(db, staff, "fhir.validate", "fhir_record", record.id,
                      {"status": status, "errors": sum(i["severity"] == "error" for i in issues), "version": record.version})
                db.commit()
                errors = [i["message"] for i in issues if i["severity"] == "error"][:2]
                print(f"  {record.record_type} #{record.id}: {status}" + (f" ({'; '.join(errors)})" if errors else ""))


TEAM_BY_HOSPITAL_ID: dict[int, dict] = {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=3, help="validator processes in parallel (default 3)")
    args = parser.parse_args()
    if settings.abdm_mode != "simulation":
        sys.exit("ABDM_MODE must be simulation: this script links records through the simulated ABDM gateway")
    if not fhir_validation.validator_available():
        sys.exit("The HL7 FHIR validator is not installed: run scripts\\setup_validator.ps1 first")
    api = Api()

    with SessionLocal() as db:
        if "abdm_hip_id" not in {c["name"] for c in inspect(db.get_bind()).get_columns("hospitals")}:
            sys.exit("The database is not upgraded yet: run python init_db.py --demo first")
        hospitals = {code: hospital_by_code(db, code) for code in TEAM}
        if any(h is None for h in hospitals.values()):
            sys.exit("The demo hospitals are missing: run python init_db.py --demo first")
        TEAM_BY_HOSPITAL_ID.update({h.id: TEAM[code] for code, h in hospitals.items()})
        hospital_ids = {code: h.id for code, h in hospitals.items()}

        # 1. visits and FHIR documents
        new_records = []
        for code, address, doctor, approver, consultation, medicines, lab in VISITS:
            hospital = hospitals[code]
            patient = patient_at(db, hospital, address)
            if patient is None:
                print(f"  {hospital.code}: no patient with {address}; skipped")
                continue
            done = db.scalar(select(Encounter.id).where(Encounter.patient_id == patient.id, Encounter.status == "approved",
                                                        Encounter.chief_complaint == consultation["chief_complaint"]))
            if done:
                print(f"  {hospital.code}: visit of {patient.full_name} ({address}) already recorded")
                continue
            usable = [u for u in (doctor, approver) if api.can_sign_in(u)]
            if not usable:
                print(f"  {hospital.code}: cannot sign in as {doctor} or {approver} with the demo password; "
                      f"visit of {patient.full_name} skipped")
                continue
            if doctor not in usable:
                print(f"  {hospital.code}: cannot sign in as {doctor} with the demo password "
                      f"(password changed or two-step sign-in); {usable[0]} sees the patient instead")
            doctor, approver = usable[0], usable[-1]
            sources = record_visit(api, code, patient, doctor, approver, consultation, medicines, lab)
            for source_type, source_id in sources:
                record = api("POST", "/fhir/records", TEAM[code]["hims"], {"source_type": source_type, "source_id": source_id})
                new_records.append(record["id"])
            print(f"  {hospital.code}: {patient.full_name} ({address}) seen by {doctor}: consultation, prescription, "
                  f"{lab[0]} report, {len(sources)} FHIR documents")
        db.expire_all()
        pending = [r.id for r in db.scalars(select(FhirRecord).where(
            FhirRecord.hospital_id.in_(TEAM_BY_HOSPITAL_ID), FhirRecord.validation_status != "valid"))]
    if pending:
        validate_all(pending, max(1, args.workers))

    # 2. ABHA verification and care context linking, at every hospital (through simulated ABDM)
    print("Verifying ABHA addresses with ABDM (simulated) and linking validated records:")
    with SessionLocal() as db:
        for code, hospital_id in hospital_ids.items():
            hospital, hims = db.get(Hospital, hospital_id), TEAM[code]["hims"]
            for patient in db.scalars(select(Patient).where(Patient.hospital_id == hospital.id,
                                                            Patient.abha_address.is_not(None)).order_by(Patient.id)).all():
                overview = api("GET", f"/hip/patients/{patient.id}", hims)
                if not (overview["link"] and overview["link"]["status"] == "verified"):
                    result = api("POST", f"/hip/patients/{patient.id}/link-token", hims)
                    overview = api("GET", f"/hip/patients/{patient.id}", hims)
                    if not (overview["link"] and overview["link"]["status"] == "verified"):
                        print(f"  {hospital.code} {patient.full_name}: not verified ({result['message']})")
                        continue
                unlinked = [c["id"] for c in overview["care_contexts"] if c["link_status"] != "linked"]
                if unlinked:
                    api("POST", f"/hip/patients/{patient.id}/care-contexts/link", hims, {"care_context_ids": unlinked})
                    overview = api("GET", f"/hip/patients/{patient.id}", hims)
                linked = sum(c["link_status"] == "linked" for c in overview["care_contexts"])
                print(f"  {hospital.code} {patient.full_name} {patient.abha_address}: verified, "
                      f"{linked} record(s) linked")
    print("Done. Sign in at one hospital and request a patient's records from the others on External records (ABDM HIU).")


if __name__ == "__main__":
    main()

"""FHIR conformance report: build one bundle per ABDM record profile and validate it with the HL7 validator.

Run from the backend folder:  python scripts\\fhir_conformance.py
Uses synthetic content and transient (unsaved) registry objects; no database or patient data involved.
"""

import base64
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fhir import (FHIR_VERSION, IG_PACKAGE, LOINC, AllergyData, AttachmentData, ClinicalContent, Coding,  # noqa: E402
                      ConditionData, EncounterData, ImmunizationData, MedicationData, ObservationData, OrderData,
                      ReportData, ResultData, build_bundle)
from app.fhir_validation import validate  # noqa: E402
from app.models import Doctor, Hospital, Patient  # noqa: E402

hospital = Hospital(id=1, name="Conformance Hospital", code="CNF", hfr_id="IN0000000001", city="Pune", state="Maharashtra",
                    pincode="411001", phone="+912012345678")
doctor = Doctor(id=1, full_name="Dr. Test Doctor", registration_number="REG-0001", hpr_id="71-0000-0000-0001",
                qualification="MBBS", specialization="General Medicine")
patient = Patient(id=1, patient_code="CNF-000001", full_name="Test Patient", gender="F", year_of_birth=1985, mobile="9000000001",
                  abha_number="91000000000001", abha_verified=True)
when = datetime(2026, 1, 15, 10, 30)
pdf = AttachmentData(content_type="application/pdf", data=base64.b64encode(b"%PDF-1.4 synthetic").decode(), title="scan.pdf")
encounter = EncounterData(start=datetime(2026, 1, 15, 10))
meds = [MedicationData(name="Paracetamol 500 mg tablet", dosage="1 tablet", route="oral", frequency="TDS", duration_days=3, quantity=9)]
report = ReportData(name="Haemoglobin", loinc=Coding(system=LOINC, code="718-7", display="Hemoglobin [Mass/volume] in Blood"),
                    results=[ResultData(name="Haemoglobin", loinc=Coding(system=LOINC, code="718-7", display="Hemoglobin [Mass/volume] in Blood"),
                                        value="11.0", value_num=11.0, unit="g/dL", ref_low=12, ref_high=15, interpretation="L")],
                    conclusion="Mild anaemia", issued=when, effective=when, interpreter=1)
vitals = [ObservationData(name="Heart rate", key="pulse", value=80, unit="/min"),
          ObservationData(name="Body weight", key="weight", value=62, unit="kg")]

CASES = {
    "OPConsultRecord": ClinicalContent(record_type="OPConsultRecord", key="op", title="OP consultation", date=when, encounter=encounter,
                                       conditions=[ConditionData(text="Fever")], allergies=[AllergyData(text="Penicillin")],
                                       observations=vitals, medications=meds, orders=[OrderData(name="CBC", authored=when)],
                                       chief_complaint="Fever for 2 days", plan="Fluids", attachment=pdf),
    "PrescriptionRecord": ClinicalContent(record_type="PrescriptionRecord", key="rx", title="Prescription", date=when,
                                          encounter=encounter, medications=meds, attachment=pdf),
    "DiagnosticReportRecord": ClinicalContent(record_type="DiagnosticReportRecord", key="dr", title="Lab report", date=when,
                                              orders=[OrderData(name="Haemoglobin", authored=when)], reports=[report], attachment=pdf),
    "DischargeSummaryRecord": ClinicalContent(record_type="DischargeSummaryRecord", key="ds", title="Discharge summary", date=when,
                                              encounter=EncounterData(start=datetime(2026, 1, 10), end=datetime(2026, 1, 14), inpatient=True),
                                              conditions=[ConditionData(text="Pneumonia", verification="confirmed")],
                                              medications=meds, reports=[report], hospital_course="Recovered", attachment=pdf),
    "ImmunizationRecord": ClinicalContent(record_type="ImmunizationRecord", key="im", title="Immunization", date=when,
                                          immunizations=[ImmunizationData(vaccine="Hepatitis B vaccine", occurrence="2025-12-01", dose_number=1)]),
    "WellnessRecord": ClinicalContent(record_type="WellnessRecord", key="wr", title="Wellness", date=when, observations=vitals),
    "HealthDocumentRecord": ClinicalContent(record_type="HealthDocumentRecord", key="hd", title="Referral letter", date=when,
                                            attachment=pdf),
}

if __name__ == "__main__":
    print(f"FHIR {FHIR_VERSION}, IG {IG_PACKAGE}\n")
    print(f"{'Profile':26} {'Result':8} {'Errors':>6} {'Warnings':>8}  Entries")
    failed = False
    for profile, content in CASES.items():
        bundle = build_bundle(content, patient, hospital, doctor, [doctor])
        status, issues, validator = validate(bundle)
        errors = [i for i in issues if i["severity"] == "error"]
        warnings = [i for i in issues if i["severity"] == "warning"]
        failed |= status != "valid"
        print(f"{profile:26} {status:8} {len(errors):>6} {len(warnings):>8}  {len(bundle['entry'])}")
        for issue in errors + warnings:
            print(f"    {issue['severity']}: {issue['location']} | {issue['message'][:160]}")
    print(f"\nValidator: {validator}")
    sys.exit(1 if failed else 0)

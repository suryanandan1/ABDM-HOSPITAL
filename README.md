# ABDM Hospital Interoperability Platform

A local hospital system that keeps its own clinical records and exchanges them through India's Ayushman Bharat
Digital Mission (ABDM) under patient consent, both as a **Health Information Provider (HIP)** and as a
**Health Information User (HIU)**, with AI assistance that is always reviewed by staff.

| Area | What it does |
|---|---|
| Hospital | Hospital profile, staff, doctors, role-based access, audit log |
| Patients | Registration with duplicate checks, search, ABHA identifiers (never trusted until verified) |
| Clinical | Consultations (vitals, diagnoses), prescriptions with doctor approval, lab catalogue, orders, samples, results, report approval, medical history |
| AI documents | Upload PDF/scan/image/DOCX/TXT → Mistral OCR + Mistral extracts → staff review every field → doctor approves |
| Terminology | LOINC suggestions from the existing mapping project (`E:\project\mapping`), confirmed by people |
| FHIR | FHIR R4 4.0.1 document Bundles for the 7 ABDM record profiles (IG `ndhm.in` 6.5.0), validated with the official HL7 validator |
| HIP | ABHA verification via ABDM, care contexts, discovery, patient-initiated linking, consent checks, encrypted sharing |
| HIU | AI-assisted consent requests, artefact validation, asynchronous encrypted retrieval, sealed storage, timeline, lab trends |
| Assistant | Patient timeline, doctor briefing, summaries and Q&A that cite their source records |

## Architecture

```
React + MUI (frontend/)  ──/api──▶  FastAPI (backend/app)
                                     ├─ routers/        hospital, staff, patients, clinical, lab, documents, fhir, assistant
                                     ├─ extraction.py   AI document extraction (review schema)      ┐ AI (ai.py → Mistral)
                                     ├─ assistant.py    read-only record assistant                  ┘ suggestions only
                                     ├─ fhir.py         FHIR engine (pure mapping)  ◀─ fhir_sources.py (records → engine input)
                                     ├─ fhir_validation.py  local pre-checks + HL7 validator (Java, tools/)
                                     ├─ loinc.py        wraps E:\project\mapping\map.py (unchanged)
                                     ├─ hip/            gateway client, crypto, HIP workflows, simulator, callbacks
                                     └─ hiu/            HIU workflows, sealed records, AI, simulator, callbacks
SQLite (hospital.db) via SQLAlchemy — switch DATABASE_URL for PostgreSQL
ABDM gateway (https://dev.abdm.gov.in/api/hiecm) ◀── HIP/HIU calls; callbacks arrive at <bridge URL>/api/v3/...
```

AI never writes clinical records, approves anything or decides access; FHIR mapping and validation contain no
business logic; HIP and HIU share one gateway client, transaction log and encryption module.

**Stack**: Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite, PyJWT, cryptography, httpx, Mistral AI SDK,
pandas/rapidfuzz (LOINC mapper); React 18, TypeScript, Vite 5, Material UI 6, React Router, Axios; HL7 FHIR
Validator 7.x on a bundled Java 21 runtime.

## Installation (Windows, PowerShell)

Prerequisites: Python 3.11+, Node.js 20+, internet for the first validator run. The LOINC mapping project is
expected at `E:\project\mapping` (set `LOINC_MAPPING_DIR` otherwise).

```powershell
cd E:\project\abdm-hospital\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET in .env; set ADMIN_PASSWORD
powershell -ExecutionPolicy Bypass -File scripts\setup_validator.ps1   # Java 21 + HL7 validator + IG into backend\tools
python init_db.py --demo          # tables, roles, hospital, admin, demo staff/doctors/patients/lab catalogue

cd ..\frontend
npm install
Copy-Item .env.example .env
```

`python init_db.py` is safe to re-run: it only creates missing tables and records (there are no migrations yet;
changed columns need a fresh database).

## Running

```powershell
# Terminal 1 – backend
cd E:\project\abdm-hospital\backend; .\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000

# Terminal 2 – frontend
cd E:\project\abdm-hospital\frontend
npm run dev
```

Open http://localhost:5173. API documentation (all 121 operations, try-it-out): http://localhost:8000/docs.

Demo logins (password `Demo@1234`): `dr.mehta`, `dr.rao` (doctors), `reception`, `labtech`, `hims`
(health information staff); `admin` uses `ADMIN_PASSWORD`.

## Configuration (`backend/.env`)

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL (default SQLite file) |
| `JWT_SECRET` | ≥32 random characters; the example value is refused at start-up |
| `CORS_ORIGINS` | Frontend origins |
| `MISTRAL_API_KEY`, `AI_MODEL`, `AI_OCR_MODEL` | Mistral AI for extraction, suggestions, summaries (default `mistral-large-latest`); PDFs and images are read with `mistral-ocr-latest`. Without a key, AI features say so and staff work manually |
| `UPLOAD_DIR`, `MAX_UPLOAD_MB` | Uploaded documents |
| `LOINC_MAPPING_DIR` | Folder with `map.py` and `data/Loinc.csv` |
| `FHIR_VALIDATOR_JAR`, `JAVA_PATH` | HL7 validator (defaults to `backend/tools`) |
| `FHIR_TX_SERVER` | Terminology server for validation and ICD-10 checks (`https://tx.fhir.org`; `n/a` = offline, no code checks) |
| `ABDM_MODE` | `simulation` (default) or `sandbox` |
| `ABDM_GATEWAY_URL`, `ABDM_CM_ID` | `https://dev.abdm.gov.in/api/hiecm`, `sbx` |
| `ABDM_CLIENT_ID`, `ABDM_CLIENT_SECRET` | NHA bridge credentials (secret never logged or returned) |
| `ABDM_HIP_ID`, `ABDM_HIU_ID` | Facility IDs registered as HIP / HIU services |
| `ABDM_BRIDGE_URL` | Public https URL of this backend; callbacks at `<url>/api/v3/...`, HIU data at `<url>/api/hiu/data-push/<token>` |
| `ABDM_VERIFY_CALLBACKS` | Verify gateway JWTs on callbacks (keep `true`) |
| `ABDM_OTP_TEST_DISPLAY` | Sandbox testing only: show linking OTPs when no SMS gateway exists |
| `HIU_AUTO_VALIDATE` | HL7-validate received bundles in the background |

**ABDM sandbox**: register with NHA (sandbox.abdm.gov.in), register the facility as HIP and HIU services, set the
`ABDM_*` values, set the hospital's HFR ID to `ABDM_HIP_ID`, expose the backend over https, then on *ABDM HIP →
Overview* use "Check gateway session" and "Register bridge URL".

## Workflows

**Clinical** – Patient → Start consultation → vitals, diagnoses (ICD-10 optional), plan → Finalize. Prescriptions:
draft → submit → doctor reviews and approves. Lab: order → collect sample → enter results (flags computed) →
conclusion → submit → a different doctor approves.

**AI documents** – AI Documents → upload → extraction → review screen (original beside fields; each field shows AI
confidence, evidence quote, status and AI/manual source) → accept/edit/reject, link patient and doctor, confirm
LOINC suggestions → submit → doctor approves → FHIR Bundle generated.

**FHIR** – FHIR Records: JSON, resources, local pre-checks and *Validate* (HL7 validator, FHIR 4.0.1, `ndhm.in#6.5.0`,
terminology via `FHIR_TX_SERVER`). A record is *HIP eligible* only when its current version validates.

**HIP** (ABDM HIP page) – (1) configuration status, (2) verify the patient's ABHA address with ABDM (link token) or let
the patient link from the ABHA app with an OTP, (3–5) care contexts are created from HIP-eligible records and linked,
(6–7) discovery requests are answered with exactly one matching patient's unlinked care contexts (no record content),
(8) consent notifications are checked against the HIP's linked records, (9–10) health-information requests within a
granted, unexpired consent get only matching records, encrypted (ECDH Curve25519 + HKDF + AES-GCM), with a status
notification per care context.

**HIU** (External Records) – (1) pick a patient with a verified ABHA, (2) give the reason; *Suggest with AI* proposes
purpose, categories and dates for review, (3) consent request, (4) the patient decides, (5–6) the artefact is fetched
and must match what was requested, (7) health-information request with fresh keys and a one-time push URL,
(8–9) asynchronous push decrypted, (10) pre-checks plus background HL7 validation, (11) originals stored sealed
(AES-GCM) and erased at the consent's dataEraseAt or on revocation, (12) shown only to doctors/administrators, every
view audited; timeline, lab trends and a source-cited AI summary.

**Assistant** – Patient profile → *Assistant briefing* (no AI: active conditions, recent consultations, prescriptions,
labs, external records, missing information) → *Open assistant* for the timeline, lab results over time, an AI history
summary and questions. AI statements are labelled *Documented* or *AI interpretation*, must cite records the user may
see (others are removed), keep only dates found in those records, and say when information is unavailable.

## Testing

```powershell
cd E:\project\abdm-hospital\backend; .\.venv\Scripts\Activate.ps1
pytest                              # everything (about 7–8 minutes; HL7 tests need backend\tools)
pytest -k "not hl7 and not e2e"     # quick run (about 2 minutes)
pytest tests\test_e2e.py            # scenarios A–D end to end with the HL7 validator
python scripts\fhir_conformance.py  # validate one bundle per ABDM record profile
$env:ABDM_LIVE_CLIENT_ID="SBX_..."; $env:ABDM_LIVE_CLIENT_SECRET="..."; pytest tests\test_hip.py -k live
```

Tests use a temporary database; AI calls are replaced by fakes (no data leaves the machine); ABDM runs in simulation;
the sandbox contract tests check exact URLs, headers and bodies against mocked HTTP.

**FHIR conformance** (`scripts/fhir_conformance.py`, HL7 FHIR Validator 7.0.4, FHIR 4.0.1, `ndhm.in#6.5.0`,
terminology tx.fhir.org): OPConsultRecord, PrescriptionRecord, DiagnosticReportRecord, DischargeSummaryRecord,
ImmunizationRecord, WellnessRecord, HealthDocumentRecord – all valid, 0 errors, 0 warnings. Bundles generated from
real workflows (scenarios A, B, D, retrieved records) are validated in the tests.

## Security review (Phase 6)

| Area | State |
|---|---|
| Authentication | JWT (HS256, expiring), scrypt password hashes, password policy, sign-in throttling (5 failures / 15 min → 429), equal-cost failures for unknown users |
| Authorization | Permission checks on every endpoint, hospital scoping on every record lookup, separate permission for viewing external records |
| Secrets | `.env` only (gitignored); example JWT secret refused; ABDM secret, link tokens, keys and OTPs never returned or logged |
| Logs | Audit log stores ids, field names and counts, not patient details; ABDM transaction log redacted; access logs drop query strings and push tokens |
| Uploads | Extension allow-list, magic-byte check, size limit, random storage names, served with fixed content types |
| Callbacks | Gateway JWT verified against ABDM's JWKS plus X-HIP-ID / X-HIU-ID; HIU data push needs a one-time token, closed after use |
| Consent | HIP shares only within granted, unexpired consent scope; HIU continues only with a validated artefact; data erased on expiry/revocation |
| Encryption | Fidelius-compatible ECDH/HKDF/AES-GCM (verified against Fidelius test vectors); retrieved records sealed at rest |
| HTTP | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on the API |
| Errors | Plain-language errors; unexpected errors return a generic 500 without internals |

## Database (27 tables)

Hospital: `hospitals, roles, users, doctors, patients, audit_logs` · Clinical: `encounters, conditions, prescriptions,
prescription_items, lab_tests, lab_orders, lab_reports, lab_results` · AI/FHIR: `clinical_documents, fhir_records,
term_mappings` · HIP: `hip_patient_links, care_contexts, hip_link_requests, hip_consents, hip_transactions` ·
HIU: `hiu_consent_requests, hiu_consent_artefacts, hiu_data_requests, retrieved_records, hiu_patient_summaries`.

## Integration status and simulated features

- **Simulated** (ABDM_MODE=simulation, labelled SIMULATION everywhere): the ABDM gateway and consent manager, the
  patient's ABHA app (ABHA accounts, discovery, OTP SMS, consent decisions), an external HIU receiving our data, and an
  external HIP ("Sunrise Clinic (SIMULATED)") whose synthetic records we retrieve.
- **ABDM sandbox**: implemented from NHA's Milestone 3 sandbox documentation (v2.5) and NHA's ABDM wrapper; verified
  only with contract tests against mocked HTTP. **Not yet exercised against the live sandbox** (no credentials).

## Known limitations

- ABHA M1 OTP login (ABHA number + Aadhaar/mobile OTP) is not implemented: no official specification was available;
  ABHA addresses are verified through ABDM link tokens or patient-initiated linking instead.
- Patient-initiated linking in sandbox needs an SMS gateway (not included).
- Consent artefact signatures are not verified (no documented consent-manager key); callbacks are authenticated by JWT.
- Data-push checksum semantics are undocumented; MD5 of the FHIR content is used.
- No database migrations; SQLite by default; simulator state is in memory.
- Stored external records are sealed with a key derived from `JWT_SECRET` (rotating it makes them unreadable).
- HL7 validation takes about a minute per bundle; background validation is not durable across restarts.
- Medicines have no drug codes (text only); ICD-10 codes need the terminology server for their official display.
- AI features need `MISTRAL_API_KEY`; documents and questions are then sent to Mistral AI. They were tested with
  fakes only. AI confidence values are model estimates, not calibrated probabilities.
- Sign-in throttling is per process and can lock a username for 15 minutes; tokens are not revoked on password change.
- The frontend has no automated UI tests.

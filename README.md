# ABDM Hospital Interoperability Platform

A local hospital system that keeps its own clinical records and exchanges them through India's Ayushman Bharat
Digital Mission (ABDM) under patient consent, both as a **Health Information Provider (HIP)** and as a
**Health Information User (HIU)**, with AI assistance that is always reviewed by staff.

| Area | What it does |
|---|---|
| Hospital | Hospital profile, staff, doctors, role-based access, audit log; cookie sessions with idle sign-out and optional two-step sign-in (authenticator app) |
| Patients | Registration with duplicate checks, search, ABHA identifiers (never trusted until verified) |
| Clinical | Consultations (vitals, diagnoses), prescriptions with doctor approval, lab catalogue, orders, samples, results, report approval, medical history |
| AI documents | Upload PDF/scan/image/DOCX/TXT → Mistral OCR + Mistral extracts → staff review every field → doctor approves |
| Terminology | LOINC suggestions from the existing mapping project (`E:\project\mapping`), confirmed by people |
| FHIR | FHIR R4 4.0.1 document Bundles for the 7 ABDM record profiles (IG `ndhm.in` 6.5.0), validated with the official HL7 validator |
| HIP | ABHA verification via ABDM, care contexts, discovery, patient-initiated linking, consent checks, encrypted sharing |
| HIU | AI-assisted consent requests, artefact validation, asynchronous encrypted retrieval, sealed storage, timeline, lab trends |
| Assistant | Patient timeline, doctor briefing, summaries and Q&A that cite their source records |

![Dashboard](docs/screenshots/02-dashboard.png)

**Contents**: [Architecture](#architecture) · [Screenshots](#screenshots) · [Flow diagrams](#flow-diagrams) ·
[Installation](#installation-windows-powershell) · [Running](#running) · [Configuration](#configuration-backendenv) ·
[Workflows](#workflows) · [Testing](#testing) · [Security review](#security-review-phase-6) ·
[Database](#database-29-tables) · [Integration status](#integration-status-and-simulated-features) ·
[Known limitations](#known-limitations)

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

## Screenshots

Taken from the running app (Microsoft Edge, 1440×900) in **simulation mode with synthetic demo data**
(`python init_db.py --demo` plus a scripted demo visit). No real patients appear anywhere. Click an image to see it
full size.

### Sign-in and dashboard

| Sign-in | Dashboard (doctor) |
|---|---|
| ![Sign-in page](docs/screenshots/01-login.png) | ![Dashboard with patient, ABHA, staff and lab counters](docs/screenshots/02-dashboard.png) |
| Username and password; a 6-digit authenticator code is asked next when two-step sign-in is on. After 30 minutes without activity the app returns here with a notice. | Counters for patients, ABHA verification, staff, today's consultations, open lab orders and reports waiting for approval; recent patients. The menu shows only what the signed-in role may use. |

### Patients

| Patient list | Patient registration |
|---|---|
| ![Patient list with search and ABHA status](docs/screenshots/03-patients.png) | ![Registration form](docs/screenshots/05-patient-registration.png) |
| Search by name, patient ID or mobile. ABHA chips show *No ABHA*, *ABHA unverified* or *ABHA verified*. | Demographics, contact and optional ABHA number/address, with a duplicate check before saving. An entered ABHA is never trusted until ABDM verifies it. |

| Patient profile | Medical history |
|---|---|
| ![Patient profile](docs/screenshots/04-patient-detail.png) | ![Medical history](docs/screenshots/10-patient-history.png) |
| Demographics, ABHA number/address with how and when it was verified, and the assistant briefing (no AI): conditions, consultations, prescriptions, recent lab results and external records, each with its source. | Only finalized or approved records: active conditions, consultations, prescriptions and lab reports. |

### Clinical work

| Consultation | Prescription |
|---|---|
| ![Finalized consultation with vitals and diagnoses](docs/screenshots/06-consultation.png) | ![Approved prescription](docs/screenshots/07-prescription.png) |
| Vitals, complaint, history, findings, diagnoses with ICD-10 codes, plan and follow-up. Prescriptions and lab orders for the visit sit on the right. Finalized consultations are locked. | Medicines with dose, route, frequency and duration. Draft → submitted → approved by the prescribing doctor. |

| Lab orders (lab technician) | Lab report |
|---|---|
| ![Lab order work list](docs/screenshots/08-lab-orders.png) | ![Approved lab report with flags](docs/screenshots/09-lab-report.png) |
| Work list by status: ordered, sample collected, in progress, completed. | Results with units, reference ranges and automatic *Low/Normal/High* flags. Approved by a doctor other than the person who entered the results. |

### AI documents and assistant

| Document review | Patient assistant |
|---|---|
| ![Document review: original beside extracted fields](docs/screenshots/12b-document-review.png) | ![Assistant with timeline and lab trends](docs/screenshots/11-assistant.png) |
| The original document beside the fields to fill. Here no AI key is configured, so the page says so and staff enter the data manually; with Mistral configured, each field shows the AI value, its confidence and the quoted evidence. | Timeline of own and external records, lab values over time, questions and an AI summary that must cite its sources. Read-only. |

### FHIR

| FHIR records | FHIR bundle |
|---|---|
| ![FHIR record list](docs/screenshots/13-fhir-records.png) | ![Resources of an OPConsultRecord bundle](docs/screenshots/14-fhir-record.png) |
| One record per approved consultation, prescription, lab report or document, with its version and validation state. | The 18 resources of an OPConsultRecord bundle. *Valid* plus *Eligible for HIP sharing* means the current version passed the HL7 validator. |

### ABDM: sharing our records (HIP) and receiving others' (HIU)

| HIP overview | HIP care contexts |
|---|---|
| ![HIP configuration and counters](docs/screenshots/15-abdm-hip.png) | ![Linked care contexts](docs/screenshots/15b-hip-care-contexts.png) |
| Configuration checks and counters for links, care contexts, consents and transfers. The *SIMULATION* label appears on every simulated item. | Validated records become care contexts linked to the patient's ABHA address. Only these can ever be shared. |

| HIU requests | HIU retrieved records |
|---|---|
| ![HIU dashboard and request history](docs/screenshots/16-abdm-hiu.png) | ![Records retrieved from another provider](docs/screenshots/16b-hiu-retrieved-records.png) |
| Consent requests and their progress in plain language. | Records received under consent: source, category, consent and erase date, plus a timeline and repeated lab tests (HbA1c trend). Visible to doctors and administrators only, and every view is audited. |

### Administration and security

| Staff | Audit log |
|---|---|
| ![Staff list](docs/screenshots/17-staff.png) | ![Audit log](docs/screenshots/18-audit-logs.png) |
| Accounts, roles, active state and a *2-step* chip. Editing a user can reset their password or two-step sign-in. | Who did what and when, without patient details. |

| Two-step sign-in setup |
|---|
| ![Two-step sign-in setup dialog](docs/screenshots/19-two-step-setup.png) |
| Account menu → *Two-step sign-in*: add the key (or open the link) in an authenticator app, then confirm a code. The key shown belongs to a throw-away demo database. |

## Flow diagrams

The diagrams are written in [Mermaid](https://mermaid.js.org/), which GitHub, GitLab and VS Code (with a Mermaid
extension) draw as pictures.

### 1. System overview

```mermaid
flowchart TB
    UI["Browser: React + MUI (frontend/)"]
    SEC["security.py<br/>session cookie + CSRF · role permissions · hospital scoping"]
    UI -- "/api" --> SEC

    subgraph Backend["FastAPI backend (backend/app)"]
        CORE["routers/<br/>hospital · staff · patients<br/>consultations · prescriptions · lab · audit"]
        SMART["extraction.py · assistant.py · loinc.py<br/>AI + terminology (suggestions only)"]
        FHIR["fhir_sources.py → fhir.py → fhir_validation.py<br/>FHIR bundles + validation"]
        ABDMM["hip/ (share) · hiu/ (receive)<br/>gateway client · encryption · sealing"]
    end
    SEC --> CORE & SMART & FHIR & ABDMM

    DB[("SQLite / PostgreSQL<br/>29 tables + uploads/")]
    AI["Mistral AI<br/>(optional)"]
    MAP["LOINC mapping<br/>project"]
    VAL["HL7 FHIR Validator<br/>+ terminology server"]
    GW["ABDM gateway +<br/>consent manager"]
    OTHER["Other hospitals<br/>(HIP / HIU)"]

    CORE --> DB
    SMART --> AI
    SMART --> MAP
    FHIR --> VAL
    ABDMM <-- "requests + callbacks /api/v3/..." --> GW
    ABDMM <-- "encrypted records" --> OTHER
```

Every backend module reads and writes the same database (only one arrow is drawn to keep the picture readable).

### 2. Roles and what they can do

```mermaid
flowchart LR
    ADMIN["Hospital administrator"] --> ALL["Everything, including staff,<br/>hospital profile, HIP configuration"]
    DOC["Doctor"] --> D1["Consultations, prescriptions,<br/>lab orders, approve lab reports,<br/>approve documents, FHIR,<br/>HIU requests + external records"]
    REC["Receptionist"] --> R1["Register, search and<br/>update patients"]
    LAB["Lab technician"] --> L1["Lab catalogue, samples,<br/>results, documents, LOINC"]
    HIMS["Health information staff"] --> H1["ABHA verification, audit log,<br/>documents, FHIR, HIP linking,<br/>HIU requests (not record content)"]
```

Permissions live in `backend/app/security.py` (`ROLES`); every endpoint checks one and every lookup is limited to
the user's hospital. Finalizing or approving clinical records also needs a linked, active doctor profile.

### 3. Sign-in, sessions and two-step sign-in

```mermaid
sequenceDiagram
    autonumber
    actor U as Staff member
    participant B as Browser (React)
    participant A as /api/auth
    participant DB as Database

    U->>B: username + password
    B->>A: POST /auth/login
    A->>DB: failures for this username + address in the last 15 min?
    alt too many (5 per username+address, 20 per address, 50 per username)
        A-->>B: 429 Try again in 15 minutes
    else
        A->>A: verify scrypt hash (same cost for unknown users)
        alt wrong password
            A->>DB: record failure + audit auth.login_failed
            A-->>B: 401 Invalid username or password
        else two-step sign-in on, no code yet
            A-->>B: 401 mfa_required
            U->>B: 6-digit code from authenticator app
            B->>A: POST /auth/login (with otp)
            A->>A: TOTP check, code not used before
        end
        A->>DB: clear failures, upgrade old hash, create user_sessions row
        A-->>B: Set-Cookie abdm_session (HttpOnly, Secure, SameSite=Strict)<br/>+ abdm_csrf (readable by the app)
    end
    Note over B,A: Every later request sends the cookie. Changes (POST/PUT/PATCH/DELETE)<br/>also send X-CSRF-Token = abdm_csrf, so other sites cannot forge them.
    B->>A: GET /api/... (cookie)
    A->>DB: session exists, not revoked, not expired, used in the last 30 min?
    A-->>B: data, or 401 → back to sign-in
    Note over B: No mouse/keyboard activity for 30 min in any tab → the app signs out by itself
    U->>B: Sign out / Sign out everywhere
    B->>A: POST /auth/logout (this session) or /auth/logout-all (all sessions)
```

A password change or reset, a role change, deactivation and turning on two-step sign-in all end the user's other
sessions. Roles listed in `MFA_REQUIRED_ROLES` see only the two-step setup screen until it is done.

### 4. Patient journey (clinical workflow)

```mermaid
flowchart TD
    REG["Receptionist registers patient<br/>(duplicate check)"] --> ABHA{"ABHA given?"}
    ABHA -- yes --> VER["Health information staff verify it<br/>with ABDM (HIP page)"]
    ABHA -- no --> CON
    VER --> CON["Doctor starts consultation (draft):<br/>vitals, complaint, findings, diagnoses (ICD-10)"]
    CON --> RX["Prescription draft → submit"]
    RX --> RXA["Prescribing doctor approves"]
    CON --> ORD["Lab orders (routine / urgent)"]
    ORD --> SMP["Lab technician collects sample<br/>(barcode) or rejects it"]
    SMP --> RES["Results entered → flags computed<br/>(Low / Normal / High) + conclusion"]
    RES --> SUB["Report submitted"]
    SUB --> APR{"Another doctor reviews"}
    APR -- approve --> DONE["Report approved, order completed"]
    APR -- reject with reason --> RES
    CON --> FIN["Doctor finalizes consultation (locked)"]
    RXA & DONE & FIN --> HIST["Medical history, assistant timeline"]
    RXA & DONE & FIN --> FHIR["FHIR record can be generated (see 6)"]
```

### 5. Record lifecycles

```mermaid
stateDiagram-v2
    direction LR
    state "Consultations, prescriptions, lab reports, AI documents (ClinicalRecord)" as CR {
        [*] --> draft
        draft --> pending_approval: submit
        rejected --> pending_approval: fix + resubmit
        pending_approval --> approved: doctor approves
        pending_approval --> rejected: doctor rejects (reason)
        draft --> cancelled
        pending_approval --> cancelled
        approved --> [*]
    }
```

Consultations skip the approval step: *Finalize* moves them from draft straight to approved, and they are locked.
Prescriptions are approved by the prescribing doctor; lab reports by a doctor who did not enter the results.

```mermaid
stateDiagram-v2
    direction LR
    state "Lab order" as LO {
        [*] --> ordered
        ordered --> sample_collected: sample collected
        sample_collected --> ordered: sample rejected (recollect)
        sample_collected --> in_progress: results entered
        in_progress --> completed: report approved
        ordered --> cancelled
        sample_collected --> cancelled
    }
```

```mermaid
stateDiagram-v2
    direction LR
    state "HIU consent request" as HR {
        [*] --> requested
        requested --> pending_consent: consent manager accepted
        pending_consent --> granted: patient grants
        pending_consent --> denied: patient denies
        granted --> retrieving: health information requested
        retrieving --> received: data pushed and decrypted
        retrieving --> failed
        granted --> revoked: patient revokes (records erased)
        received --> revoked
        received --> expired: dataEraseAt reached (records erased)
    }
```

### 6. AI document processing and FHIR generation

```mermaid
flowchart TD
    UP["Upload PDF / scan / image / DOCX / TXT"] --> CHK["Checks: extension allow-list,<br/>magic bytes, size limit, random file name"]
    CHK --> KEY{"MISTRAL_API_KEY set?"}
    KEY -- no --> MAN["Review screen: manual entry"]
    KEY -- yes --> OCR["Mistral OCR (PDF/images)<br/>or text extraction (DOCX/TXT)"]
    OCR --> EX["Mistral extraction into the review schema<br/>value + confidence + evidence quote per field"]
    EX --> REV["Review screen: original beside fields<br/>staff accept / edit / reject every field"]
    MAN --> REV
    REV --> LNK["Link patient + responsible doctor,<br/>confirm LOINC suggestions (loinc.py)"]
    LNK --> SUBM["Submit"] --> DAP{"Doctor approves?"}
    DAP -- no --> REV
    DAP -- yes --> GEN

    subgraph FHIRGEN["FHIR (same for consultations, prescriptions, lab reports)"]
        GEN["fhir_sources.py: approved record → engine input"] --> MAPF["fhir.py: FHIR R4 document Bundle<br/>(OPConsult, Prescription, DiagnosticReport,<br/>DischargeSummary, Immunization, Wellness,<br/>HealthDocument)"]
        MAPF --> PRE["Local pre-checks"]
        PRE --> HL7["HL7 validator: FHIR 4.0.1 + ndhm.in 6.5.0<br/>terminology via FHIR_TX_SERVER"]
        HL7 --> OKV{"0 errors?"}
        OKV -- yes --> ELIG["Valid → eligible for HIP sharing"]
        OKV -- no --> FIX["Fix the source record → regenerate (new version)"]
    end
```

AI only suggests. People confirm every value, a doctor approves, and only then is a FHIR record made.

### 7. HIP: sharing this hospital's records through ABDM

```mermaid
sequenceDiagram
    autonumber
    actor S as Health info staff
    participant H as This hospital (HIP)
    participant G as ABDM gateway / consent manager
    participant P as Patient (ABHA app)
    participant U as Requesting HIU

    S->>H: verify patient's ABHA address
    H->>G: link token request
    G-->>H: callback: link token (ABHA verified)
    S->>H: link care contexts (validated FHIR records only)
    H->>G: add care contexts
    Note over P,G: Or the patient starts it: discovery → exactly one matching patient → OTP → linked
    P->>G: grant consent to an HIU
    G->>H: consent notification (HIP checks it covers linked records)
    U->>G: health-information request (with its public key)
    G->>H: request: consent id, date range, HIU key, push URL
    H->>H: consent granted and unexpired? records inside its scope only
    H->>H: encrypt each bundle: ECDH Curve25519 + HKDF + AES-GCM
    H->>U: push encrypted data to the HIU's URL
    H->>G: status notification per care context
```

Callbacks from the gateway are only accepted with a valid gateway JWT (checked against ABDM's keys) and the
right `X-HIP-ID`. Discovery answers never contain record content.

### 8. HIU: getting a patient's records from other providers

```mermaid
sequenceDiagram
    autonumber
    actor D as Doctor
    participant H as This hospital (HIU)
    participant G as ABDM gateway / consent manager
    participant P as Patient (ABHA app)
    participant X as Other hospital (HIP)

    D->>H: patient with verified ABHA + reason
    opt Suggest with AI
        H-->>D: proposed purpose, record types, dates (age/gender only sent to AI)
    end
    D->>H: confirm and send consent request
    H->>G: consent request init
    G->>P: ask the patient
    P-->>G: grant (maybe fewer record types) or deny
    G-->>H: consent notification
    H->>G: fetch artefact
    G-->>H: artefact: must match what was requested, else refused
    H->>H: fresh key pair + one-time push token
    H->>G: health-information request
    G->>X: request with our public key and push URL
    X->>H: encrypted bundles → /api/hiu/data-push/{one-time token}
    H->>H: decrypt, checksum, keep only consented types and dates
    H->>H: seal originals (AES-GCM, RECORD_SEALING_KEY), pre-checks, background HL7 validation
    H->>G: transfer notification
    D->>H: view records (doctors/admins only, every view audited)
    Note over H: Records are erased at the consent's dataEraseAt or when the patient revokes
```

### 9. Patient assistant

```mermaid
flowchart LR
    REQ["Doctor opens assistant"] --> PERM["Collect only records this user may see<br/>(own approved records; external records<br/>only with hiu:records)"]
    PERM --> BRIEF["Briefing + timeline + lab trends<br/>(no AI)"]
    PERM --> Q["Question or summary request"]
    Q --> KEYQ{"AI configured?"}
    KEYQ -- no --> NOAI["Says AI is unavailable"]
    KEYQ -- yes --> LLM["Mistral: answer citing record ids"]
    LLM --> FILT["Remove citations to records the user<br/>may not see; keep only dates found in records"]
    FILT --> OUT["Statements labelled 'Documented'<br/>or 'AI interpretation', with sources"]
```

### 10. Data model (main tables)

```mermaid
erDiagram
    hospitals ||--o{ users : employs
    roles ||--o{ users : grants
    users ||--o{ user_sessions : "signs in"
    users |o--o| doctors : "linked profile"
    hospitals ||--o{ patients : registers
    patients ||--o{ encounters : has
    doctors ||--o{ encounters : conducts
    encounters ||--o{ conditions : diagnoses
    encounters ||--o{ prescriptions : produces
    prescriptions ||--|{ prescription_items : contains
    encounters ||--o{ lab_orders : orders
    lab_tests ||--o{ lab_orders : "test ordered"
    lab_orders ||--o| lab_reports : "report"
    lab_reports ||--|{ lab_results : contains
    patients ||--o{ clinical_documents : "uploaded for"
    patients ||--o{ fhir_records : "FHIR bundles"
    fhir_records ||--o| care_contexts : "shared as"
    patients ||--o{ hip_patient_links : "ABHA link"
    patients ||--o{ hip_consents : "HIP consents"
    patients ||--o{ hiu_consent_requests : "HIU requests"
    hiu_consent_requests ||--o{ hiu_consent_artefacts : "granted as"
    hiu_consent_artefacts ||--o{ hiu_data_requests : "data requested"
    hiu_data_requests ||--o{ retrieved_records : receives
    hospitals ||--o{ audit_logs : records
```

`login_failures`, `hip_link_requests`, `hip_transactions`, `term_mappings` and `hiu_patient_summaries` are left
out of the picture for readability; all 29 tables are listed under [Database](#database-29-tables).

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

`init_db.py` refuses to run while `ADMIN_PASSWORD` in `.env` still has the example value. Upgrading from a version
before cookie sessions (migration `0003`) signs everyone out once; they simply sign in again.

`python init_db.py` is safe to re-run: it applies new database migrations (Alembic, `backend/migrations`) and only
adds missing records. Databases created before migrations existed are recognised and upgraded in place. After changing
`app/models.py`, create a migration with `alembic revision --autogenerate -m "what changed"`, review it, and run
`python init_db.py` (or `alembic upgrade head`); `tests/test_migrations.py` fails if a model change has no migration.

## Running

```powershell
# Terminal 1 – backend
cd E:\project\abdm-hospital\backend; .\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000

# Terminal 2 – frontend
cd E:\project\abdm-hospital\frontend
npm run dev
```

**Behind a reverse proxy (https)**: run `uvicorn app.main:app --proxy-headers --forwarded-allow-ips=<proxy address>`
so sign-in throttling sees real client addresses and HSTS is sent. Serve `frontend/dist` with the headers in
`PRODUCTION_HEADERS` (`frontend/vite.config.ts`: CSP incl. `frame-ancestors 'none'`, `nosniff`, `X-Frame-Options`,
`Referrer-Policy`) and forward `/api` to the backend; `npm run build; npm run preview` shows the result locally.

Open http://localhost:5173. API documentation (all 127 operations, try-it-out; use *Authorize* with the `access_token` from `/api/auth/login`): http://localhost:8000/docs.

Demo logins (password `Demo@1234`): `dr.mehta`, `dr.rao` (doctors), `reception`, `labtech`, `hims`
(health information staff); `admin` uses `ADMIN_PASSWORD`. Two-step sign-in is off for the demo users unless
`MFA_REQUIRED_ROLES` names their role; the app signs out after `SESSION_IDLE_MINUTES` (30) without activity.

## Configuration (`backend/.env`)

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL (default SQLite file) |
| `JWT_SECRET` | ≥32 random characters; the example value is refused at start-up |
| `JWT_EXPIRE_MINUTES`, `SESSION_IDLE_MINUTES` | Longest a sign-in lasts (480) and how long an unused session survives (30); the browser also signs out after that long without activity |
| `COOKIE_SECURE` | Session cookie only over https (default `true`; browsers accept it on `http://localhost` too) |
| `PASSWORD_SCRYPT_N` | scrypt cost for password hashes (default 32768); older hashes are upgraded at the next sign-in |
| `MFA_REQUIRED_ROLES`, `MFA_ISSUER` | Roles that must use two-step sign-in, e.g. `hospital_admin,doctor` (anyone can opt in); the name shown in authenticator apps |
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
| `RECORD_SEALING_KEY`, `RECORD_SEALING_OLD_KEYS` | Key for records received from other providers (sealed at rest), separate from `JWT_SECRET`; earlier keys stay readable during a rotation. After setting or rotating it, run `python scriptseseal_records.py`. Unset = legacy key derived from `JWT_SECRET` |

**ABDM sandbox**: register with NHA (sandbox.abdm.gov.in), register the facility as HIP and HIU services, set the
`ABDM_*` values, set the hospital's HFR ID to `ABDM_HIP_ID`, expose the backend over https, then on *ABDM HIP →
Overview* use "Check gateway session" and "Register bridge URL".

## Workflows

**Sign-in and accounts** – Sign in with username and password (plus a 6-digit code once two-step sign-in is on).
The account menu (top right) has *Change password*, *Two-step sign-in* (set up: add the shown key or link to an
authenticator app such as Google/Microsoft Authenticator or FreeOTP, confirm a code; turn off: password + code),
*Sign out* (this computer) and *Sign out everywhere* (all sessions). Users whose role is in `MFA_REQUIRED_ROLES` see
only the setup screen until it is done. Lost phone: an administrator opens the user on *Staff* → *Reset two-step
sign-in*; the user sets it up again at the next sign-in. Five wrong attempts from one computer block that username
there for 15 minutes.

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
pytest -k "not hl7 and not e2e"     # quick run (about 2–3 minutes)
pytest tests\test_auth_security.py  # sessions, CSRF, idle timeout, throttling, password hashes, two-step sign-in
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
| Authentication | Sessions (JWT HS256 naming a server-side session) in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie with a double-submit CSRF token; Bearer tokens for API clients. *Sign out* ends that session, *Sign out everywhere* all of them; changing or resetting a password, a role change or deactivation end every session. Sessions end after `SESSION_IDLE_MINUTES` unused, and the browser signs out after the same time without activity. scrypt password hashes (n=32768, upgraded at sign-in), password policy, equal-cost failures for unknown users. Optional TOTP two-step sign-in (authenticator app; can be required per role, admins can reset it, codes work once, secrets sealed at rest). Throttling stored in the database: 5 failures per username and address, 20 per address, 50 per username in 15 min → 429, so guesses from one computer do not lock the user out elsewhere |
| Authorization | Permission checks on every endpoint, hospital scoping on every record lookup, separate permission for viewing external records |
| Secrets | `.env` only (gitignored); example JWT secret refused; ABDM secret, link tokens, keys and OTPs never returned or logged |
| Logs | Audit log stores ids, field names and counts, not patient details; ABDM transaction log redacted; access logs drop query strings and push tokens |
| Uploads | Extension allow-list, magic-byte check, size limit, random storage names, served with fixed content types |
| Callbacks | Gateway JWT verified against ABDM's JWKS plus X-HIP-ID / X-HIU-ID; HIU data push needs a one-time token, closed after use |
| Consent | HIP shares only within granted, unexpired consent scope; HIU continues only with a validated artefact; data erased on expiry/revocation |
| Encryption | Fidelius-compatible ECDH/HKDF/AES-GCM (verified against Fidelius test vectors); retrieved records sealed at rest (AES-GCM) with a dedicated, rotatable key; each sealed value names its key id |
| HTTP | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; `Strict-Transport-Security` on https; on the API `Cache-Control: no-store` and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; sandbox`; the production build carries a strict CSP `<meta>` (scripts only from the app itself) |
| Errors | Plain-language errors; unexpected errors return a generic 500 without internals |

## Database (29 tables)

Hospital: `hospitals, roles, users, user_sessions, login_failures, doctors, patients, audit_logs` · Clinical: `encounters, conditions, prescriptions,
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
- SQLite by default; simulator state is in memory.
- Without `RECORD_SEALING_KEY`, stored external records are sealed with a key derived from `JWT_SECRET` (rotating it
  would make them unreadable); set the sealing key and run `scriptseseal_records.py` to separate them.
- HL7 validation takes about a minute per bundle; background validation is not durable across restarts.
- Medicines have no drug codes (text only); ICD-10 codes need the terminology server for their official display.
- AI features need `MISTRAL_API_KEY`; documents and questions are then sent to Mistral AI. They were tested with
  fakes only. AI confidence values are model estimates, not calibrated probabilities.
- Two-step sign-in shows the setup key and an `otpauth://` link, not a QR code; there are no recovery codes (an administrator resets it).
- The frontend has no automated UI tests.

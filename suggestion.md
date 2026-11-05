# Suggestions for the ABDM Hospital Platform

All improvement suggestions for this project live in this one file. They are grouped by area and ordered by
priority inside each group: **High** should be fixed soon, **Medium** is worth planning, **Low** is cleanup.

When you finish a suggestion, tick it off (`- [x]`) or delete it, so this file stays a current to-do list.

_Last reviewed: 2026-11-05 (branch `feature/token-revocation-migrations`)._

---

## 1. Fix now (small, quick wins)

- [x] **High – README has a broken command.** In two places the README says `python scriptseseal_records.py`.
  The `\r` in `scripts\reseal_records.py` was turned into a carriage return. It should read
  `python scripts\reseal_records.py` (Configuration table and Known limitations).
- [x] **High – README contradicts itself about tokens.** *Known limitations* still says "tokens are not revoked on
  password change", but the Security table (and `backend/app/routers/auth.py`) now revoke them. Remove that
  half-sentence.
- [ ] **High – Commit the current work.** The token-revocation, Alembic migration and record-sealing changes
  (15 modified files plus `alembic.ini`, `migrations/`, `reseal_records.py`, two new test files) are not committed
  yet. The repository has only one commit, so there is nothing to go back to if something breaks.
- [ ] **Low – Line endings.** Git warns that LF will become CRLF in some files. Add a `.gitattributes`
  (e.g. `* text=auto eol=lf`, `*.ps1 text eol=crlf`) so diffs don't fill up with line-ending noise.

## 2. Security

_All done on 2026-11-05: see the Security review table in the README for how each one works._

- [x] **High – "Sign out" ends every session.** `POST /auth/logout` calls `revoke_tokens`, which bumps
  `token_version`, so signing out on one computer also signs the user out on every other computer. On shared ward
  machines this is surprising. Suggest: make normal logout end only the current token (add a `jti` claim and a
  small revoked-tokens table, or short-lived tokens), and keep "Sign out everywhere" as a separate button.
- [x] **High – Sign-in throttling can lock out real staff.** Throttling is by username only
  (`_failures` in `auth.py`), so anyone can type five wrong passwords for `dr.mehta` and block that doctor for
  15 minutes. Suggest also throttling by client IP, and/or adding a growing delay instead of a hard lock.
- [x] **Medium – Throttling state is in memory.** It resets on restart and is not shared if you run more than one
  uvicorn worker. Store failures in the database (or Redis) before running more than one process.
- [x] **Medium – The JWT is stored in `localStorage`.** Any XSS bug would let a script read it. The CSP lowers this
  risk a lot, but for a system holding patient data consider an `HttpOnly`, `Secure`, `SameSite=Strict` cookie
  (plus CSRF protection) instead.
- [x] **Medium – Long session, no idle timeout.** Tokens last 480 minutes (`JWT_EXPIRE_MINUTES`). Add an idle
  timeout in the frontend (e.g. sign out after 15–30 minutes without activity) and/or shorter tokens with a refresh
  step. Unattended logged-in screens are a common hospital risk.
- [x] **Medium – Add `Strict-Transport-Security`** (HSTS) when the app is served over https, and send the CSP as a
  real HTTP header from whatever serves the built frontend (the `<meta>` tag can't set `frame-ancestors`, as the
  comment in `vite.config.ts` already notes).
- [x] **Low – Consider stronger password hashing parameters.** scrypt `n=2**14` is the minimum commonly
  recommended. Raising it to `2**15`/`2**16` (and storing the parameters inside the hash string so old hashes still
  verify) adds margin cheaply.
- [x] **Low – Multi-factor sign-in for administrators and doctors** (TOTP) once the system is used for real.
- [x] **Low – Plan the default `RECORD_SEALING_KEY` away.** While it is unset, sealed records depend on
  `JWT_SECRET`. Consider refusing to start in `sandbox` mode without a dedicated sealing key, so nobody forgets.

## 3. Reliability and data

- [ ] **High – Background HL7 validation is not durable.** `hiu/service.py` starts a daemon `threading.Thread`; if
  the server restarts, queued validations are lost silently. Suggest: store a `validation_status = pending` on the
  record and, at start-up, re-queue anything still pending (or use a small job queue).
- [ ] **Medium – Move to PostgreSQL before real use.** SQLite is fine for development, but it handles concurrent
  writes poorly. The code is ready (`DATABASE_URL`), and Alembic now makes the switch safe. Run the test suite
  against PostgreSQL once to be sure.
- [ ] **Medium – Backups.** Document (and script) a backup of the database **and** `backend/uploads/`, plus a tested
  restore. Note that sealed records need the sealing key(s) to be backed up separately and safely.
- [ ] **Medium – Simulator state is in memory.** Fine for demos, but it means a restart in simulation mode loses
  in-flight consent/linking flows. At least mention this clearly on the simulation screens.
- [ ] **Low – Retention for audit and transaction logs.** Decide how long `audit_logs` and `hip_transactions` are
  kept and add an archive/cleanup job, so the tables do not grow forever.

## 4. Code quality and maintainability

- [ ] **Medium – Pin dependency versions.** `requirements.txt` mostly uses `>=` (FastAPI, SQLAlchemy, PyJWT,
  cryptography, httpx…). A future major release could break the app on a fresh install. Use exact pins (or a
  lock file via `pip-tools`/`uv`) and update them on purpose.
- [ ] **Medium – Remove hard-coded `E:\project\...` paths** from the README and defaults (`LOINC_MAPPING_DIR`
  defaults to a sibling folder). Use relative paths in docs so the project works on any drive or machine.
- [ ] **Medium – Split the largest files.** `models.py` (~680 lines), `schemas.py` (~630), `fhir.py` (~610),
  `hip/service.py` (~570) and `frontend/src/api.ts` (~500) are getting hard to navigate. Split by area
  (hospital / clinical / documents / hip / hiu), the way the routers already are.
- [ ] **Low – Add a linter and formatter.** Backend: `ruff` (lint + format). Frontend: ESLint + Prettier. Run them
  locally or as a pre-commit hook (no CI needed).
- [ ] **Low – Type-check the backend** with `mypy` or `pyright`; the code already uses type hints.

## 5. Testing

- [ ] **Medium – Frontend has no automated tests** (also listed in Known limitations). Start with a few
  Playwright tests for the most important paths: sign in, register a patient, consultation → finalize,
  lab order → approve, AI document review → approve.
- [ ] **Medium – Faster default test run.** The full suite takes 7–8 minutes because of the HL7 validator. Mark
  those tests (`@pytest.mark.hl7`, `@pytest.mark.e2e`) and make the quick run the default in `pytest.ini`, with the
  slow run opt-in.
- [x] **Low – Test the sign-in throttle and logout behaviour** explicitly, especially after changing either of them
  as suggested in section 2.
- [ ] **Low – Run the live ABDM sandbox tests** as soon as NHA credentials are available; the sandbox integration is
  so far verified only against mocked HTTP.

## 6. Features and product

- [ ] **Medium – Drug codes for medicines.** Prescriptions are text only. Add a medicine master with codes
  (e.g. SNOMED CT) so FHIR `MedicationRequest`s are coded and interoperable.
- [ ] **Medium – ABHA M1 (create/login with Aadhaar or mobile OTP)** when NHA's specification is available.
- [ ] **Medium – SMS gateway integration** so patient-initiated linking works in the sandbox without
  `ABDM_OTP_TEST_DISPLAY`.
- [ ] **Low – Verify consent artefact signatures** once the consent manager's public key is documented.
- [ ] **Low – Printable views** for prescriptions, lab reports and discharge summaries (patients still ask for paper).
- [ ] **Low – Accessibility pass** on the frontend (keyboard navigation, labels, contrast), and later
  Hindi / regional-language UI text.

## 7. Deployment and operations

- [ ] **Medium – Production run guide.** Document how to run in production: uvicorn/gunicorn behind a reverse proxy
  (nginx/Caddy) with https, serving `frontend/dist`, security headers, and `ABDM_BRIDGE_URL` exposure.
- [ ] **Medium – Health check that means something.** `/api/health` always returns `ok`. Add a readiness check that
  confirms the database is reachable (and optionally the validator/AI configuration) for monitoring.
- [ ] **Low – Structured logs** (JSON) with request ids, to make tracing a single ABDM transaction across HIP/HIU
  logs easier — keeping the current rule of no patient details in logs.
- [ ] **Low – Optional Docker setup** (`docker-compose` with backend, frontend, PostgreSQL) for a one-command
  start on a new machine.

---

_Note: CI/CD pipelines are intentionally not suggested here — they were declined for this project._

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    # Stored as naive UTC so SQLite and PostgreSQL behave the same; API schemas add the UTC offset.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(20), unique=True)  # prefix for patient IDs
    hfr_id: Mapped[str | None] = mapped_column(String(50), unique=True)  # ABDM Health Facility Registry ID
    facility_type: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on sign-out, password change/reset, role change and deactivation; tokens carry the version they
    # were issued with, so every older token stops working at once.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Two-step sign-in (TOTP, RFC 6238). The secret is sealed like external records (app.hiu.records.seal); it is
    # stored during setup and only used for sign-in once mfa_enabled is set. mfa_last_step blocks code replay.
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    mfa_last_step: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    hospital: Mapped[Hospital] = relationship(lazy="joined")
    role: Mapped[Role] = relationship(lazy="joined")

    @property
    def role_code(self) -> str:
        return self.role.code

    @property
    def role_name(self) -> str:
        return self.role.name


class UserSession(Base):
    """One sign-in. Tokens name their session, so signing out ends just that one; idle sessions expire."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # random, carried in the token as "sid"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class LoginFailure(Base):
    """Failed sign-ins, for throttling. Kept in the database so limits hold across restarts and workers."""

    __tablename__ = "login_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), index=True)
    ip_address: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (UniqueConstraint("hospital_id", "registration_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)  # optional login account
    full_name: Mapped[str] = mapped_column(String(200))
    registration_number: Mapped[str] = mapped_column(String(50))  # medical council registration
    hpr_id: Mapped[str | None] = mapped_column(String(50))  # ABDM Healthcare Professionals Registry ID
    specialization: Mapped[str | None] = mapped_column(String(100))
    qualification: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User | None] = relationship()

    @property
    def username(self) -> str | None:
        return self.user.username if self.user else None


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("hospital_id", "abha_number"),
        UniqueConstraint("hospital_id", "abha_address"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_code: Mapped[str] = mapped_column(String(30), unique=True)  # internal patient ID (MRN)
    full_name: Mapped[str] = mapped_column(String(200), index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    year_of_birth: Mapped[int] = mapped_column(Integer)  # always set; derived from age when DOB unknown
    gender: Mapped[str] = mapped_column(String(1))  # M, F, O, U (ABDM codes)
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    address_line: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6))

    # ABHA identifiers are stored as entered and only trusted once abha_verified is True.
    abha_number: Mapped[str | None] = mapped_column(String(14))
    abha_address: Mapped[str | None] = mapped_column(String(100))
    abha_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    abha_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    abha_verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    abha_verification_method: Mapped[str | None] = mapped_column(String(50))

    emergency_contact_name: Mapped[str | None] = mapped_column(String(200))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))
    emergency_contact_relation: Mapped[str | None] = mapped_column(String(50))

    registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)  # registration date
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    registered_by: Mapped[User | None] = relationship(foreign_keys=[registered_by_id])
    abha_verified_by: Mapped[User | None] = relationship(foreign_keys=[abha_verified_by_id])

    @property
    def registered_by_name(self) -> str | None:
        return self.registered_by.full_name if self.registered_by else None

    @property
    def abha_verified_by_name(self) -> str | None:
        return self.abha_verified_by.full_name if self.abha_verified_by else None


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50), index=True)  # e.g. patient.create
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(50))
    details: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User | None] = relationship()

    @property
    def user_name(self) -> str | None:
        return self.user.full_name if self.user else None


# ---------- clinical records (Phase 2) ----------
# Each model notes the FHIR R4 resource it maps to; the mapping itself lives in app/fhir.py.

class ClinicalRecord:
    """Workflow columns shared by records that need approval.

    status: draft -> pending_approval -> approved, or rejected / cancelled.
    """

    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(300))  # why it was rejected or cancelled
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @declared_attr
    def created_by(cls) -> Mapped[User]:
        return relationship(foreign_keys=f"{cls.__name__}.created_by_id")

    @declared_attr
    def approved_by(cls) -> Mapped[User | None]:
        return relationship(foreign_keys=f"{cls.__name__}.approved_by_id")

    @property
    def created_by_name(self) -> str:
        return self.created_by.full_name

    @property
    def approved_by_name(self) -> str | None:
        return self.approved_by.full_name if self.approved_by else None


class Encounter(ClinicalRecord, Base):
    """Doctor consultation (FHIR Encounter, documented as an OPConsultRecord Composition).
    Uses draft -> approved (finalized) or cancelled."""

    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    visit_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    chief_complaint: Mapped[str | None] = mapped_column(Text)
    history_present_illness: Mapped[str | None] = mapped_column(Text)
    clinical_findings: Mapped[str | None] = mapped_column(Text)
    vitals: Mapped[dict | None] = mapped_column(JSON)  # keys of app.fhir.VITAL_SIGNS, each -> an Observation
    treatment_plan: Mapped[str | None] = mapped_column(Text)
    follow_up_instructions: Mapped[str | None] = mapped_column(Text)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    doctor_notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped[Patient] = relationship()
    doctor: Mapped[Doctor] = relationship()
    conditions: Mapped[list["Condition"]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan", order_by="Condition.id")


class Condition(Base):
    """Diagnosis recorded during a consultation (FHIR Condition)."""

    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(primary_key=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    display: Mapped[str] = mapped_column(String(300))  # diagnosis as written by the doctor
    code: Mapped[str | None] = mapped_column(String(20))  # ICD-10 code, when known
    clinical_status: Mapped[str] = mapped_column(String(20), default="active")  # active | recurrence | inactive | resolved
    verification_status: Mapped[str] = mapped_column(String(20), default="provisional")  # provisional | confirmed | differential
    onset_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    encounter: Mapped[Encounter] = relationship(back_populates="conditions")


class Prescription(ClinicalRecord, Base):
    """Prescription (FHIR PrescriptionRecord Composition with one MedicationRequest per item)."""

    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    prescription_date: Mapped[date] = mapped_column(Date, default=lambda: utcnow().date())
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped[Patient] = relationship()
    doctor: Mapped[Doctor] = relationship()
    encounter: Mapped[Encounter] = relationship()
    items: Mapped[list["PrescriptionItem"]] = relationship(cascade="all, delete-orphan", order_by="PrescriptionItem.id")


class PrescriptionItem(Base):
    """One medicine line (FHIR MedicationRequest)."""

    __tablename__ = "prescription_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    medicine_name: Mapped[str] = mapped_column(String(200))
    dosage: Mapped[str] = mapped_column(String(100))  # e.g. "500 mg" or "1 tablet"
    route: Mapped[str] = mapped_column(String(30))  # key of app.fhir.ROUTES
    frequency: Mapped[str] = mapped_column(String(30))  # key of app.fhir.FREQUENCIES
    duration_days: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    instructions: Mapped[str | None] = mapped_column(String(300))


class LabTest(Base):
    """Laboratory test catalogue entry. Each parameter becomes one Observation in a report."""

    __tablename__ = "lab_tests"
    __table_args__ = (UniqueConstraint("hospital_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100))
    sample_type: Mapped[str] = mapped_column(String(50))
    loinc_code: Mapped[str | None] = mapped_column(String(20))
    # [{code, name, unit, loinc_code, ref_low, ref_high, ref_text}]; copied into each result when entered
    parameters: Mapped[list] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LabOrder(Base):
    """A test ordered during a consultation, with its sample (FHIR ServiceRequest + Specimen)."""

    __tablename__ = "lab_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    test_id: Mapped[int] = mapped_column(ForeignKey("lab_tests.id"))
    priority: Mapped[str] = mapped_column(String(10), default="routine")  # routine | urgent | stat
    clinical_notes: Mapped[str | None] = mapped_column(String(500))
    # ordered -> sample_collected -> in_progress -> completed, or cancelled
    status: Mapped[str] = mapped_column(String(20), default="ordered", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(300))
    sample_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | collected | rejected
    sample_barcode: Mapped[str | None] = mapped_column(String(50))
    sample_collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    sample_collected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    sample_notes: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient] = relationship()
    doctor: Mapped[Doctor] = relationship()
    test: Mapped[LabTest] = relationship()
    sample_collected_by: Mapped[User | None] = relationship(foreign_keys=[sample_collected_by_id])
    report: Mapped["LabReport | None"] = relationship(back_populates="order")

    @property
    def sample_collected_by_name(self) -> str | None:
        return self.sample_collected_by.full_name if self.sample_collected_by else None


class LabReport(ClinicalRecord, Base):
    """Laboratory report (FHIR DiagnosticReport inside a DiagnosticReportRecord Composition)."""

    __tablename__ = "lab_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("lab_orders.id"), unique=True)
    approved_by_doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))  # resultsInterpreter
    conclusion: Mapped[str | None] = mapped_column(Text)

    order: Mapped[LabOrder] = relationship(back_populates="report")
    approved_by_doctor: Mapped[Doctor | None] = relationship()
    results: Mapped[list["LabResult"]] = relationship(cascade="all, delete-orphan", order_by="LabResult.id")


class LabResult(Base):
    """One measured parameter (FHIR Observation). Name, unit and range are copied from the catalogue."""

    __tablename__ = "lab_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("lab_reports.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    loinc_code: Mapped[str | None] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(200))
    value_numeric: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    ref_text: Mapped[str | None] = mapped_column(String(100))
    interpretation: Mapped[str | None] = mapped_column(String(2))  # N normal, L low, H high, A abnormal


# ---------- AI document processing and FHIR records (Phase 3) ----------

class ClinicalDocument(ClinicalRecord, Base):
    """An uploaded clinical document, its AI extraction and the staff-reviewed record.
    status: draft (in review) -> pending_approval -> approved | rejected (back to review) | cancelled."""

    __tablename__ = "clinical_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), index=True)
    author_doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))  # doctor responsible for the content
    approved_by_doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))
    document_type: Mapped[str] = mapped_column(String(30), default="other")
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    storage_name: Mapped[str] = mapped_column(String(100))  # file name inside UPLOAD_DIR
    text: Mapped[str | None] = mapped_column(Text)  # extracted text of DOCX/TXT files
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | completed | failed
    extraction_error: Mapped[str | None] = mapped_column(String(500))
    ai_model: Mapped[str | None] = mapped_column(String(50))
    ai_output: Mapped[dict | None] = mapped_column(JSON)  # untouched AI result, kept for traceability
    data: Mapped[dict | None] = mapped_column(JSON)  # current reviewed record (app.extraction.ReviewedRecord)

    patient: Mapped[Patient | None] = relationship()
    author_doctor: Mapped[Doctor | None] = relationship(foreign_keys=[author_doctor_id])
    approved_by_doctor: Mapped[Doctor | None] = relationship(foreign_keys=[approved_by_doctor_id])


class FhirRecord(Base):
    """Generated FHIR document Bundle for an approved record, with its latest validation result."""

    __tablename__ = "fhir_records"
    __table_args__ = (UniqueConstraint("source_type", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(20))  # consultation | prescription | lab_order | document
    source_id: Mapped[int] = mapped_column(Integer)
    record_type: Mapped[str] = mapped_column(String(40))  # ABDM profile, e.g. PrescriptionRecord
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    bundle: Mapped[dict] = mapped_column(JSON)
    prechecks: Mapped[list] = mapped_column(JSON, default=list)  # fast local checks, see app.fhir_validation
    validation_status: Mapped[str] = mapped_column(String(20), default="not_validated")  # valid | invalid | error
    validation_issues: Mapped[list | None] = mapped_column(JSON)
    validator: Mapped[str | None] = mapped_column(String(200))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    generated_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped[Patient] = relationship()

    @property
    def hip_eligible(self) -> bool:
        """Approved source + clean validation of this exact bundle version."""
        return self.validation_status == "valid"


class TermMapping(Base):
    """A test name to LOINC mapping confirmed by an authorised user, reused as a suggestion."""

    __tablename__ = "term_mappings"
    __table_args__ = (UniqueConstraint("hospital_id", "source_text"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"))
    source_text: Mapped[str] = mapped_column(String(200))  # normalised test name
    loinc_code: Mapped[str] = mapped_column(String(20))
    display: Mapped[str] = mapped_column(String(300))
    confirmed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------- ABDM HIP (Phase 4) ----------

class HipPatientLink(Base):
    """A patient's ABHA address as verified by ABDM through a link token (HIP-initiated linking)."""

    __tablename__ = "hip_patient_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), unique=True)
    abha_address: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | failed
    message: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(50), index=True)
    link_token: Mapped[str | None] = mapped_column(Text)  # secret: never logged or returned by the API
    mode: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient] = relationship()


class CareContext(Base):
    """One shareable record (an HIP-eligible FHIR record) as an ABDM care context."""

    __tablename__ = "care_contexts"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    fhir_record_id: Mapped[int] = mapped_column(ForeignKey("fhir_records.id"), unique=True)
    reference: Mapped[str] = mapped_column(String(50), unique=True)  # HIP-defined careContext referenceNumber
    display: Mapped[str] = mapped_column(String(200))
    hi_type: Mapped[str] = mapped_column(String(30))  # ABDM HI type, e.g. Prescription
    record_date: Mapped[datetime] = mapped_column(DateTime)
    link_status: Mapped[str] = mapped_column(String(20), default="not_linked")  # not_linked | linking | linked | failed
    linked_abha_address: Mapped[str | None] = mapped_column(String(100))
    link_method: Mapped[str | None] = mapped_column(String(30))  # hip_initiated | patient_initiated
    linked_at: Mapped[datetime | None] = mapped_column(DateTime)
    request_id: Mapped[str | None] = mapped_column(String(50), index=True)
    message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    fhir_record: Mapped[FhirRecord] = relationship()


class HipLinkRequest(Base):
    """Patient-initiated linking: the OTP the HIP sent for a link/init, checked at link/confirm."""

    __tablename__ = "hip_link_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    transaction_id: Mapped[str] = mapped_column(String(50))
    link_reference: Mapped[str] = mapped_column(String(50), unique=True)
    abha_address: Mapped[str] = mapped_column(String(100))
    care_context_refs: Mapped[list] = mapped_column(JSON)
    otp_hash: Mapped[str] = mapped_column(String(100))
    test_otp: Mapped[str | None] = mapped_column(String(10))  # only with ABDM_OTP_TEST_DISPLAY / simulation
    otp_expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="otp_sent")  # otp_sent | linked | failed
    mode: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HipConsent(Base):
    """A consent artefact notified to this HIP by the consent manager."""

    __tablename__ = "hip_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    consent_id: Mapped[str] = mapped_column(String(50), unique=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    abha_address: Mapped[str | None] = mapped_column(String(100))
    hiu_id: Mapped[str | None] = mapped_column(String(100))
    purpose: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # GRANTED | REVOKED | EXPIRED | DENIED | REJECTED
    status_reason: Mapped[str | None] = mapped_column(String(500))
    hi_types: Mapped[list] = mapped_column(JSON, default=list)
    care_contexts: Mapped[list] = mapped_column(JSON, default=list)  # [{patientReference, careContextReference}]
    date_from: Mapped[datetime | None] = mapped_column(DateTime)
    date_to: Mapped[datetime | None] = mapped_column(DateTime)
    data_erase_at: Mapped[datetime | None] = mapped_column(DateTime)
    artefact: Mapped[dict | None] = mapped_column(JSON)  # consent detail as received (no clinical data)
    mode: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient | None] = relationship()


class HipTransaction(Base):
    """Every ABDM exchange (outbound call or inbound callback) with a plain-language outcome.
    `details` is redacted: no tokens, keys, OTPs or health record content."""

    __tablename__ = "hip_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # simulation | sandbox
    kind: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    status: Mapped[str] = mapped_column(String(20), index=True)  # pending | success | failed
    message: Mapped[str] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(50), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(50), index=True)
    consent_id: Mapped[str | None] = mapped_column(String(50), index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    http_status: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient | None] = relationship()


# ---------- ABDM HIU (Phase 5) ----------

class HiuConsentRequest(Base):
    """A request for a patient's records from other providers. Nothing is fetched without a granted consent.
    status: requested -> pending_consent -> granted | denied | revoked | expired | failed;
    granted -> retrieving -> received (or failed)."""

    __tablename__ = "hiu_consent_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requester_doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    abha_address: Mapped[str] = mapped_column(String(100))
    purpose_code: Mapped[str] = mapped_column(String(20))
    hi_types: Mapped[list] = mapped_column(JSON)
    date_from: Mapped[datetime] = mapped_column(DateTime)
    date_to: Mapped[datetime] = mapped_column(DateTime)
    data_erase_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(String(500))  # clinical reason entered by staff
    ai_suggestion: Mapped[dict | None] = mapped_column(JSON)  # what the AI proposed, kept for transparency
    request_id: Mapped[str | None] = mapped_column(String(50), index=True)  # REQUEST-ID of consent init
    consent_request_id: Mapped[str | None] = mapped_column(String(50), index=True)  # from on-init
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    status_message: Mapped[str | None] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient] = relationship()
    requester_doctor: Mapped[Doctor] = relationship()
    artefacts: Mapped[list["HiuConsentArtefact"]] = relationship(back_populates="consent_request",
                                                                 order_by="HiuConsentArtefact.id")


class HiuConsentArtefact(Base):
    """One consent artefact (one per HIP) for a request, fetched from the consent manager and validated."""

    __tablename__ = "hiu_consent_artefacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    consent_request_id: Mapped[int] = mapped_column(ForeignKey("hiu_consent_requests.id"), index=True)
    artefact_id: Mapped[str] = mapped_column(String(50), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="notified")  # notified | valid | invalid | revoked | expired
    status_message: Mapped[str | None] = mapped_column(String(500))
    fetch_request_id: Mapped[str | None] = mapped_column(String(50), index=True)
    hip_id: Mapped[str | None] = mapped_column(String(100))
    hi_types: Mapped[list | None] = mapped_column(JSON)
    care_contexts: Mapped[list | None] = mapped_column(JSON)
    date_from: Mapped[datetime | None] = mapped_column(DateTime)
    date_to: Mapped[datetime | None] = mapped_column(DateTime)
    data_erase_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[dict | None] = mapped_column(JSON)  # consentDetail as received
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)

    consent_request: Mapped[HiuConsentRequest] = relationship(back_populates="artefacts")


class HiuDataRequest(Base):
    """One health-information request against a valid artefact; data arrives later at a one-time push URL."""

    __tablename__ = "hiu_data_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    artefact_id: Mapped[int] = mapped_column(ForeignKey("hiu_consent_artefacts.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(50), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(50), index=True)  # from on-request
    push_token: Mapped[str] = mapped_column(String(64), unique=True)  # secret part of the dataPushUrl
    key_material: Mapped[str | None] = mapped_column(Text)  # sealed private key + nonce; deleted when done
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|acknowledged|received|failed|expired
    status_message: Mapped[str | None] = mapped_column(String(500))
    pages_expected: Mapped[int | None] = mapped_column(Integer)
    pages_received: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    artefact: Mapped[HiuConsentArtefact] = relationship()


class RetrievedRecord(Base):
    """A FHIR document received from another provider. The decrypted bundle is stored sealed and unmodified,
    and erased when the consent's dataEraseAt passes or the consent is revoked."""

    __tablename__ = "retrieved_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("hiu_data_requests.id"))
    consent_artefact_id: Mapped[str] = mapped_column(String(50), index=True)
    source_hip_id: Mapped[str | None] = mapped_column(String(100))
    source_name: Mapped[str | None] = mapped_column(String(200))
    care_context_reference: Mapped[str] = mapped_column(String(100))
    record_type: Mapped[str | None] = mapped_column(String(50))  # ABDM profile of the Composition
    category: Mapped[str] = mapped_column(String(50))
    record_date: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    content: Mapped[str] = mapped_column(Text)  # sealed original FHIR JSON exactly as decrypted
    checksum_ok: Mapped[bool] = mapped_column(Boolean)
    validation_status: Mapped[str] = mapped_column(String(20), default="not_validated")  # valid | invalid | error
    validation_issues: Mapped[list | None] = mapped_column(JSON)
    erase_at: Mapped[datetime] = mapped_column(DateTime)
    mode: Mapped[str] = mapped_column(String(20))

    patient: Mapped[Patient] = relationship()


class HiuPatientSummary(Base):
    """Latest AI summary of a patient's retrieved records (sealed; every statement cites record ids)."""

    __tablename__ = "hiu_patient_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), unique=True)
    content: Mapped[str] = mapped_column(Text)
    record_ids: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

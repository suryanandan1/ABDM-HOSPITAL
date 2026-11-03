import re
from datetime import date, datetime, timezone
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator

from app.fhir import FREQUENCIES, ROUTES, VITAL_SIGNS

T = TypeVar("T")


# ---------- reusable field types ----------

def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; they are stored as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def normalize_mobile(value: str) -> str:
    digits = re.sub(r"[\s\-()]", "", value)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return digits


def normalize_abha_number(value: str) -> str:
    digits = re.sub(r"[\s\-]", "", value)
    if not re.fullmatch(r"\d{14}", digits):
        raise ValueError("ABHA number must have 14 digits (e.g. 91-1234-5678-9012)")
    return digits


def normalize_abha_address(value: str) -> str:
    value = value.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._]{2,49}@[a-z]+", value):
        raise ValueError("ABHA address must look like name@abdm")
    return value


def _check_email(value: str) -> str:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Enter a valid email address")
    return value.lower()


def _check_pincode(value: str) -> str:
    if not re.fullmatch(r"[1-9]\d{5}", value):
        raise ValueError("Pincode must be 6 digits")
    return value


def _check_password(value: str) -> str:
    if len(value) < 8 or not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must be at least 8 characters and contain letters and digits")
    return value


OptStr = Annotated[str | None, BeforeValidator(_blank_to_none)]
Name = Annotated[str, BeforeValidator(_blank_to_none), Field(min_length=2, max_length=200)]
Mobile = Annotated[str, AfterValidator(normalize_mobile)]
OptMobile = Annotated[str | None, BeforeValidator(_blank_to_none),
                      AfterValidator(lambda v: v and normalize_mobile(v))]
OptPhone = Annotated[Annotated[str, Field(max_length=20)] | None, BeforeValidator(_blank_to_none)]
OptEmail = Annotated[str | None, BeforeValidator(_blank_to_none), AfterValidator(lambda v: v and _check_email(v))]
OptPincode = Annotated[str | None, BeforeValidator(_blank_to_none), AfterValidator(lambda v: v and _check_pincode(v))]
OptAbhaNumber = Annotated[str | None, BeforeValidator(_blank_to_none),
                          AfterValidator(lambda v: v and normalize_abha_number(v))]
OptAbhaAddress = Annotated[str | None, BeforeValidator(_blank_to_none),
                           AfterValidator(lambda v: v and normalize_abha_address(v))]
Password = Annotated[str, AfterValidator(_check_password)]
UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]
Gender = Literal["M", "F", "O", "U"]
RoleCode = Literal["hospital_admin", "doctor", "receptionist", "lab_technician", "health_info_staff"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------- auth / users ----------

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: Password


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    email: str | None
    phone: str | None
    is_active: bool
    role_code: str
    role_name: str
    hospital_id: int
    last_login_at: UTCDateTime | None
    created_at: UTCDateTime


class CurrentUser(UserOut):
    hospital_name: str
    permissions: list[str]
    doctor_id: int | None = None  # linked doctor profile, needed for clinical authoring/approval


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: CurrentUser


class StaffCreate(BaseModel):
    username: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{3,50}$")]
    password: Password
    full_name: Name
    email: OptEmail = None
    phone: OptPhone = None
    role: RoleCode


class StaffUpdate(BaseModel):
    full_name: Name | None = None
    email: OptEmail = None
    phone: OptPhone = None
    role: RoleCode | None = None
    is_active: bool | None = None
    password: Password | None = None  # administrator password reset


class RoleOut(ORMModel):
    code: str
    name: str
    description: str | None
    permissions: list[str] = []


# ---------- hospital ----------

class HospitalUpdate(BaseModel):
    name: Name | None = None
    hfr_id: OptStr = None
    facility_type: OptStr = None
    address: OptStr = None
    city: OptStr = None
    state: OptStr = None
    pincode: OptPincode = None
    phone: OptPhone = None
    email: OptEmail = None


class HospitalOut(ORMModel):
    id: int
    name: str
    code: str
    hfr_id: str | None
    facility_type: str | None
    address: str | None
    city: str | None
    state: str | None
    pincode: str | None
    phone: str | None
    email: str | None
    created_at: UTCDateTime


# ---------- doctors ----------

RegistrationNumber = Annotated[str, Field(min_length=3, max_length=50)]


class DoctorBase(BaseModel):
    full_name: Name
    registration_number: Annotated[RegistrationNumber, BeforeValidator(_blank_to_none)]
    hpr_id: OptStr = None
    specialization: OptStr = None
    qualification: OptStr = None
    department: OptStr = None
    phone: OptPhone = None
    email: OptEmail = None
    user_id: int | None = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(DoctorBase):
    full_name: Name | None = None
    registration_number: Annotated[RegistrationNumber | None, BeforeValidator(_blank_to_none)] = None
    is_active: bool | None = None


class DoctorOut(DoctorBase, ORMModel):
    id: int
    is_active: bool
    username: str | None = None
    created_at: UTCDateTime


# ---------- patients ----------

class PatientFields(BaseModel):
    full_name: Name
    gender: Gender
    mobile: Mobile
    date_of_birth: date | None = None
    age: Annotated[int | None, Field(ge=0, le=130)] = None  # used when DOB is unknown
    address_line: OptStr = None
    city: OptStr = None
    state: OptStr = None
    pincode: OptPincode = None
    abha_number: OptAbhaNumber = None
    abha_address: OptAbhaAddress = None
    emergency_contact_name: OptStr = None
    emergency_contact_phone: OptMobile = None
    emergency_contact_relation: OptStr = None

    @model_validator(mode="after")
    def check_dob(self):
        if self.date_of_birth and self.date_of_birth > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return self

    @property
    def year_of_birth(self) -> int | None:
        if self.date_of_birth:
            return self.date_of_birth.year
        if self.age is not None:
            return date.today().year - self.age
        return None


class PatientCreate(PatientFields):
    confirm_not_duplicate: bool = False  # set after staff reviewed the possible duplicates

    @model_validator(mode="after")
    def need_dob_or_age(self):
        if self.date_of_birth is None and self.age is None:
            raise ValueError("Provide either date of birth or age")
        return self


class PatientUpdate(PatientFields):
    full_name: Name | None = None
    gender: Gender | None = None
    mobile: Mobile | None = None


class AbhaVerifyRequest(BaseModel):
    # Phase 1 supports only staff-recorded checks; ABDM OTP/API verification comes later.
    method: Literal["physical_abha_card", "abha_app_profile"]
    note: Annotated[str, Field(min_length=5, max_length=300)]


class PatientSummary(ORMModel):
    id: int
    patient_code: str
    full_name: str
    gender: str
    date_of_birth: date | None
    year_of_birth: int
    mobile: str
    abha_number: str | None
    abha_address: str | None
    abha_verified: bool
    created_at: UTCDateTime


class PatientOut(PatientSummary):
    address_line: str | None
    city: str | None
    state: str | None
    pincode: str | None
    abha_verified_at: UTCDateTime | None
    abha_verification_method: str | None
    abha_verified_by_name: str | None = None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    emergency_contact_relation: str | None
    registered_by_name: str | None = None
    updated_at: UTCDateTime


class DuplicateCandidate(BaseModel):
    patient: PatientSummary
    level: Literal["exact", "likely", "possible"]
    reasons: list[str]


# ---------- dashboard / audit ----------

class AuditLogOut(ORMModel):
    id: int
    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict | None
    ip_address: str | None
    user_id: int | None
    user_name: str | None = None
    created_at: UTCDateTime


class DashboardStats(BaseModel):
    total_patients: int
    patients_today: int
    abha_verified_patients: int
    active_doctors: int
    active_staff: int
    consultations_today: int
    lab_orders_open: int
    lab_reports_awaiting_approval: int


class DashboardOut(BaseModel):
    stats: DashboardStats
    recent_patients: list[PatientSummary]
    recent_activity: list[AuditLogOut]


# ---------- clinical records (Phase 2) ----------

OptText = Annotated[Annotated[str, Field(max_length=5000)] | None, BeforeValidator(_blank_to_none)]
Text300 = Annotated[str, BeforeValidator(_blank_to_none), Field(min_length=1, max_length=300)]
Vitals = create_model("Vitals", **{
    key: (float | None, Field(None, ge=low, le=high, description=f"{label} ({unit})"))
    for key, (label, _loinc, unit, low, high) in VITAL_SIGNS.items()
})


class ReasonRequest(BaseModel):
    reason: Annotated[str, Field(min_length=3, max_length=300)]


class DoctorBrief(ORMModel):
    id: int
    full_name: str
    registration_number: str
    specialization: str | None
    qualification: str | None


class RecordMeta(ORMModel):
    status: str
    status_reason: str | None
    created_by_name: str
    approved_by_name: str | None
    approved_at: UTCDateTime | None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ConditionIn(BaseModel):
    display: Text300
    code: Annotated[Annotated[str, Field(pattern=r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")] | None,
                    BeforeValidator(lambda v: _blank_to_none(v.upper()) if isinstance(v, str) else v)] = None  # ICD-10
    clinical_status: Literal["active", "recurrence", "inactive", "resolved"] = "active"
    verification_status: Literal["provisional", "confirmed", "differential"] = "provisional"
    onset_date: date | None = None
    note: OptStr = None


class ConditionOut(ConditionIn, ORMModel):
    id: int
    encounter_id: int


class ConditionStatusUpdate(BaseModel):
    clinical_status: Literal["active", "recurrence", "inactive", "resolved"]


class EncounterCreate(BaseModel):
    patient_id: int
    visit_at: datetime | None = None


class EncounterUpdate(BaseModel):
    visit_at: datetime | None = None
    chief_complaint: OptText = None
    history_present_illness: OptText = None
    clinical_findings: OptText = None
    vitals: Vitals | None = None
    treatment_plan: OptText = None
    follow_up_instructions: OptText = None
    follow_up_date: date | None = None
    doctor_notes: OptText = None
    diagnoses: list[ConditionIn] | None = None  # replaces the consultation's diagnoses


class EncounterOut(RecordMeta):
    id: int
    patient: PatientSummary
    doctor: DoctorBrief
    visit_at: UTCDateTime
    chief_complaint: str | None
    history_present_illness: str | None
    clinical_findings: str | None
    vitals: dict | None
    treatment_plan: str | None
    follow_up_instructions: str | None
    follow_up_date: date | None
    doctor_notes: str | None
    conditions: list[ConditionOut]


class PrescriptionItemIn(BaseModel):
    medicine_name: Annotated[str, BeforeValidator(_blank_to_none), Field(min_length=2, max_length=200)]
    dosage: Annotated[str, BeforeValidator(_blank_to_none), Field(min_length=1, max_length=100)]
    route: Literal[tuple(ROUTES)]
    frequency: Literal[tuple(FREQUENCIES)]
    duration_days: Annotated[int, Field(ge=1, le=365)]
    quantity: Annotated[int | None, Field(ge=1, le=10000)] = None
    instructions: OptStr = None


class PrescriptionIn(BaseModel):
    items: Annotated[list[PrescriptionItemIn], Field(min_length=1)]
    notes: OptText = None


class PrescriptionCreate(PrescriptionIn):
    encounter_id: int


class PrescriptionItemOut(PrescriptionItemIn, ORMModel):
    id: int


class PrescriptionOut(RecordMeta):
    id: int
    encounter_id: int
    patient: PatientSummary
    doctor: DoctorBrief
    prescription_date: date
    notes: str | None
    items: list[PrescriptionItemOut]


class LabParameter(BaseModel):
    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,30}$")]
    name: Text300
    unit: OptStr = None
    loinc_code: OptStr = None
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: OptStr = None  # e.g. "Negative" for non-numeric results

    @model_validator(mode="after")
    def check_range(self):
        self.code = self.code.upper()
        if self.ref_low is not None and self.ref_high is not None and self.ref_low > self.ref_high:
            raise ValueError(f"{self.name}: reference low must not exceed high")
        return self


class LabTestIn(BaseModel):
    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{2,30}$")]
    name: Text300
    category: OptStr = None
    sample_type: Text300
    loinc_code: OptStr = None
    parameters: Annotated[list[LabParameter], Field(min_length=1)]
    is_active: bool = True

    @model_validator(mode="after")
    def unique_codes(self):
        self.code = self.code.upper()
        codes = [p.code for p in self.parameters]
        if len(codes) != len(set(codes)):
            raise ValueError("Parameter codes must be unique within a test")
        return self


class LabTestOut(LabTestIn, ORMModel):
    id: int


class LabOrderCreate(BaseModel):
    encounter_id: int
    test_ids: Annotated[list[int], Field(min_length=1)]
    priority: Literal["routine", "urgent", "stat"] = "routine"
    clinical_notes: OptStr = None


class SampleCollect(BaseModel):
    sample_barcode: OptStr = None
    collected_at: datetime | None = None
    notes: OptStr = None


class LabResultIn(BaseModel):
    code: str
    value: OptStr = None
    interpretation: Literal["N", "L", "H", "A"] | None = None  # only used when it cannot be calculated


class LabReportIn(BaseModel):
    results: list[LabResultIn]
    conclusion: OptText = None


class LabResultOut(ORMModel):
    code: str
    name: str
    loinc_code: str | None
    value: str
    value_numeric: float | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    interpretation: str | None


class LabReportOut(RecordMeta):
    id: int
    conclusion: str | None
    results: list[LabResultOut]


class LabOrderOut(ORMModel):
    id: int
    encounter_id: int
    patient: PatientSummary
    doctor: DoctorBrief
    test: LabTestOut
    priority: str
    clinical_notes: str | None
    status: str
    status_reason: str | None
    sample_status: str
    sample_barcode: str | None
    sample_collected_at: UTCDateTime | None
    sample_collected_by_name: str | None
    sample_notes: str | None
    created_at: UTCDateTime
    report: LabReportOut | None


class PatientHistory(BaseModel):
    patient: PatientOut
    active_conditions: list[ConditionOut]
    consultations: list[EncounterOut]
    prescriptions: list[PrescriptionOut]
    lab_orders: list[LabOrderOut]


# ---------- AI documents and FHIR records (Phase 3) ----------

class DocumentUpdate(BaseModel):
    document_type: Literal["prescription", "lab_report", "opd_consultation", "discharge_summary",
                           "immunization", "wellness", "clinical_note", "other"] | None = None
    patient_id: int | None = None
    author_doctor_id: int | None = None
    data: dict | None = None  # app.extraction.ReviewedRecord


class LoincAction(BaseModel):
    action: Literal["confirm", "reject"]
    code: str | None = None  # a corrected code; defaults to the suggested one


class DocumentSummary(RecordMeta):
    id: int
    patient: PatientSummary | None
    document_type: str
    file_name: str
    content_type: str
    file_size: int
    extraction_status: str
    extraction_error: str | None


class DocumentOut(DocumentSummary):
    author_doctor: DoctorBrief | None
    ai_model: str | None
    text: str | None
    data: dict | None
    requirements: list[str] = []  # what must be completed before submitting
    review_counts: dict[str, int] = {}
    warnings: list[str] = []  # document details that differ from the linked registered patient
    fhir_record_id: int | None = None


class FhirGenerate(BaseModel):
    source_type: Literal["consultation", "prescription", "lab_order", "document"]
    source_id: int


class FhirRecordSummary(ORMModel):
    id: int
    patient: PatientSummary
    source_type: str
    source_id: int
    record_type: str
    title: str
    version: int
    validation_status: str
    validated_at: UTCDateTime | None
    generated_at: UTCDateTime
    hip_eligible: bool


class FhirRecordOut(FhirRecordSummary):
    bundle: dict
    prechecks: list[dict]
    validation_issues: list[dict] | None
    validator: str | None

import axios from 'axios'

export const TOKEN_KEY = 'abdm_token'

export const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(undefined, (error) => {
  // Expired or invalid session: drop the token and go back to login.
  if (error.response?.status === 401 && !error.config.url?.endsWith('/auth/login')) {
    localStorage.removeItem(TOKEN_KEY)
    window.location.assign('/login')
  }
  return Promise.reject(error)
})

/** Turn a FastAPI error response into one readable message. */
export function errorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return 'Unexpected error'
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: string[]; msg: string }) => {
        const field = d.loc?.filter((p) => p !== 'body').join('.')
        return `${field ? field.replace(/_/g, ' ') + ': ' : ''}${d.msg.replace(/^Value error, /, '')}`
      })
      .join('; ')
  }
  if (detail?.message) return detail.message
  return error.message
}

// ---------- shared types (mirror backend schemas) ----------

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type RoleCode = 'hospital_admin' | 'doctor' | 'receptionist' | 'lab_technician' | 'health_info_staff'

export interface User {
  id: number
  username: string
  full_name: string
  email: string | null
  phone: string | null
  is_active: boolean
  role_code: RoleCode
  role_name: string
  hospital_id: number
  last_login_at: string | null
  created_at: string
}

export interface CurrentUser extends User {
  hospital_name: string
  permissions: string[]
  doctor_id: number | null
}

export interface Role {
  code: RoleCode
  name: string
  description: string | null
  permissions: string[]
}

export interface Hospital {
  id: number
  name: string
  code: string
  hfr_id: string | null
  facility_type: string | null
  address: string | null
  city: string | null
  state: string | null
  pincode: string | null
  phone: string | null
  email: string | null
  created_at: string
}

export interface Doctor {
  id: number
  full_name: string
  registration_number: string
  hpr_id: string | null
  specialization: string | null
  qualification: string | null
  department: string | null
  phone: string | null
  email: string | null
  user_id: number | null
  username: string | null
  is_active: boolean
  created_at: string
}

export interface PatientSummary {
  id: number
  patient_code: string
  full_name: string
  gender: 'M' | 'F' | 'O' | 'U'
  date_of_birth: string | null
  year_of_birth: number
  mobile: string
  abha_number: string | null
  abha_address: string | null
  abha_verified: boolean
  created_at: string
}

export interface Patient extends PatientSummary {
  address_line: string | null
  city: string | null
  state: string | null
  pincode: string | null
  abha_verified_at: string | null
  abha_verification_method: string | null
  abha_verified_by_name: string | null
  emergency_contact_name: string | null
  emergency_contact_phone: string | null
  emergency_contact_relation: string | null
  registered_by_name: string | null
  updated_at: string
}

export interface DuplicateCandidate {
  patient: PatientSummary
  level: 'exact' | 'likely' | 'possible'
  reasons: string[]
}

export interface AuditLog {
  id: number
  action: string
  entity_type: string | null
  entity_id: string | null
  details: Record<string, unknown> | null
  ip_address: string | null
  user_id: number | null
  user_name: string | null
  created_at: string
}

export interface Dashboard {
  stats: {
    total_patients: number
    patients_today: number
    abha_verified_patients: number
    active_doctors: number
    active_staff: number
    consultations_today: number
    lab_orders_open: number
    lab_reports_awaiting_approval: number
  }
  recent_patients: PatientSummary[]
  recent_activity: AuditLog[]
}

// ---------- display helpers ----------

export const GENDERS = { M: 'Male', F: 'Female', O: 'Other', U: 'Undisclosed' } as const

export const formatAbha = (n: string | null) => (n ? n.replace(/^(\d{2})(\d{4})(\d{4})(\d{4})$/, '$1-$2-$3-$4') : '')

export const formatDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('en-IN') : '')

export const formatDateTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('en-IN') : '')

export function ageOf(p: Pick<PatientSummary, 'date_of_birth' | 'year_of_birth'>): string {
  if (!p.date_of_birth) return `~${new Date().getFullYear() - p.year_of_birth} y`
  const dob = new Date(p.date_of_birth)
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  if (now < new Date(now.getFullYear(), dob.getMonth(), dob.getDate())) age--
  return `${age} y`
}

// ---------- clinical records (Phase 2) ----------

export type RecordStatus = 'draft' | 'pending_approval' | 'approved' | 'rejected' | 'cancelled'

export interface RecordMeta {
  status: RecordStatus
  status_reason: string | null
  created_by_name: string
  approved_by_name: string | null
  approved_at: string | null
  created_at: string
  updated_at: string
}

export interface DoctorBrief {
  id: number
  full_name: string
  registration_number: string
  specialization: string | null
  qualification: string | null
}

export interface Condition {
  id?: number
  encounter_id?: number
  display: string
  code: string | null
  clinical_status: 'active' | 'recurrence' | 'inactive' | 'resolved'
  verification_status: 'provisional' | 'confirmed' | 'differential'
  onset_date: string | null
  note: string | null
}

export interface Encounter extends RecordMeta {
  id: number
  patient: PatientSummary
  doctor: DoctorBrief
  visit_at: string
  chief_complaint: string | null
  history_present_illness: string | null
  clinical_findings: string | null
  vitals: Record<string, number | null> | null
  treatment_plan: string | null
  follow_up_instructions: string | null
  follow_up_date: string | null
  doctor_notes: string | null
  conditions: Condition[]
}

export interface PrescriptionItem {
  medicine_name: string
  dosage: string
  route: string
  frequency: string
  duration_days: number
  quantity: number | null
  instructions: string | null
}

export interface Prescription extends RecordMeta {
  id: number
  encounter_id: number
  patient: PatientSummary
  doctor: DoctorBrief
  prescription_date: string
  notes: string | null
  items: PrescriptionItem[]
}

export interface LabParameter {
  code: string
  name: string
  unit: string | null
  loinc_code: string | null
  ref_low: number | null
  ref_high: number | null
  ref_text: string | null
}

export interface LabTest {
  id: number
  code: string
  name: string
  category: string | null
  sample_type: string
  loinc_code: string | null
  parameters: LabParameter[]
  is_active: boolean
}

export interface LabResult extends Omit<LabParameter, 'code'> {
  code: string
  value: string
  value_numeric: number | null
  interpretation: 'N' | 'L' | 'H' | 'A' | null
}

export interface LabReport extends RecordMeta {
  id: number
  conclusion: string | null
  results: LabResult[]
}

export type LabOrderStatus = 'ordered' | 'sample_collected' | 'in_progress' | 'completed' | 'cancelled'

export interface LabOrder {
  id: number
  encounter_id: number
  patient: PatientSummary
  doctor: DoctorBrief
  test: LabTest
  priority: 'routine' | 'urgent' | 'stat'
  clinical_notes: string | null
  status: LabOrderStatus
  status_reason: string | null
  sample_status: 'pending' | 'collected' | 'rejected'
  sample_barcode: string | null
  sample_collected_at: string | null
  sample_collected_by_name: string | null
  sample_notes: string | null
  created_at: string
  report: LabReport | null
}

export interface PatientHistory {
  patient: Patient
  active_conditions: Condition[]
  consultations: Encounter[]
  prescriptions: Prescription[]
  lab_orders: LabOrder[]
}

export interface Terminology {
  vitals: { key: string; label: string; unit: string; min: number; max: number }[]
  routes: { key: string; label: string }[]
  frequencies: { key: string; label: string }[]
}

let terminologyRequest: Promise<Terminology> | null = null

/** Coded lists from the backend, fetched once per page load. */
export function getTerminology(): Promise<Terminology> {
  terminologyRequest ??= api.get<Terminology>('/terminology').then((r) => r.data)
  return terminologyRequest
}

export const formatRange = (r: Pick<LabParameter, 'ref_low' | 'ref_high' | 'ref_text'>) =>
  r.ref_text ??
  (r.ref_low != null && r.ref_high != null ? `${r.ref_low} – ${r.ref_high}`
    : r.ref_low != null ? `≥ ${r.ref_low}` : r.ref_high != null ? `≤ ${r.ref_high}` : '')

// ---------- AI documents and FHIR records (Phase 3) ----------

export interface ReviewField {
  value: string | null
  confidence: number | null
  evidence: string | null
  source: 'ai' | 'manual'
  status: 'extracted' | 'uncertain' | 'missing'
  review: 'pending' | 'accepted' | 'rejected' | 'edited'
}

export interface LoincMapping {
  status: 'none' | 'suggested' | 'confirmed' | 'rejected'
  code: string | null
  display: string | null
  matched_term: string | null
  confidence: number | null
  confirmed_by: string | null
}

export type FieldGroup = Record<string, ReviewField>
export type LabResultRow = FieldGroup & { loinc?: LoincMapping }

export interface ReviewedRecord {
  document_type: DocumentType
  document_date: ReviewField
  patient: FieldGroup
  doctor: FieldGroup
  encounter: FieldGroup
  diagnoses: FieldGroup[]
  allergies: FieldGroup[]
  vitals: FieldGroup[]
  medications: FieldGroup[]
  lab_results: LabResultRow[]
  immunizations: FieldGroup[]
  report_conclusion: ReviewField
  notes: ReviewField
}

export type DocumentType = 'prescription' | 'lab_report' | 'opd_consultation' | 'discharge_summary' | 'immunization'
  | 'wellness' | 'clinical_note' | 'other'

export const DOCUMENT_TYPES: Record<DocumentType, string> = {
  prescription: 'Prescription', lab_report: 'Laboratory report', opd_consultation: 'OPD consultation',
  discharge_summary: 'Discharge summary', immunization: 'Immunization record', wellness: 'Wellness record',
  clinical_note: 'Clinical note', other: 'Other health document',
}

export interface ClinicalDocument extends RecordMeta {
  id: number
  patient: PatientSummary | null
  author_doctor: DoctorBrief | null
  document_type: DocumentType
  file_name: string
  content_type: string
  file_size: number
  extraction_status: 'pending' | 'completed' | 'failed'
  extraction_error: string | null
  ai_model: string | null
  text: string | null
  data: ReviewedRecord | null
  requirements: string[]
  review_counts: Record<string, number>
  warnings: string[]
  fhir_record_id: number | null
}

export interface ValidationIssue {
  severity: 'error' | 'warning' | 'information'
  category: string
  message: string
  location: string | null
  expression?: string | null
}

export interface FhirRecord {
  id: number
  patient: PatientSummary
  source_type: 'consultation' | 'prescription' | 'lab_order' | 'document'
  source_id: number
  record_type: string
  title: string
  version: number
  validation_status: 'not_validated' | 'valid' | 'invalid' | 'error'
  validated_at: string | null
  generated_at: string
  hip_eligible: boolean
  bundle?: { entry: { fullUrl: string; resource: Record<string, any> }[] } & Record<string, any>
  prechecks?: ValidationIssue[]
  validation_issues?: ValidationIssue[] | null
  validator?: string | null
}

// ---------- ABDM HIP (Phase 4) ----------

export interface HipStatus {
  mode: 'simulation' | 'sandbox'
  hip_id: string | null
  ready: boolean
  checks: { label: string; ok: boolean; detail: string }[]
  counts: Record<string, number>
}

export interface CareContextRow {
  id: number
  patient_id: number
  reference: string
  display: string
  hi_type: string
  record_date: string
  link_status: 'not_linked' | 'linking' | 'linked' | 'failed'
  linked_abha_address: string | null
  link_method: string | null
  linked_at: string | null
  message: string | null
  fhir_record_id: number
}

export interface HipTransactionRow {
  id: number
  mode: 'simulation' | 'sandbox'
  kind: string
  direction: 'inbound' | 'outbound'
  status: 'pending' | 'success' | 'failed'
  message: string
  request_id: string | null
  transaction_id: string | null
  consent_id: string | null
  patient: PatientSummary | null
  http_status: number | null
  details: Record<string, unknown> | null
  created_at: string
}

export interface HipConsentRow {
  consent_id: string
  mode: string
  status: string
  status_reason: string | null
  patient: PatientSummary | null
  abha_address: string | null
  hiu_id: string | null
  purpose: string | null
  hi_types: string[]
  care_contexts: { patientReference: string; careContextReference: string }[]
  date_from: string | null
  date_to: string | null
  data_erase_at: string | null
  created_at: string
}

export interface HipPatientOverview {
  patient: PatientSummary
  abha_verification_method: string | null
  link: { abha_address: string; status: string; message: string | null; mode: string; updated_at: string } | null
  care_contexts: CareContextRow[]
  link_requests: { link_reference: string; status: string; abha_address: string; care_contexts: string[]; mode: string;
    expires_at: string; test_otp: string | null }[]
  consents: HipConsentRow[]
}

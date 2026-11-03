import { ReactNode, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, Grid2 as Grid, LinearProgress, MenuItem,
  Paper, Skeleton, Stack, TextField, Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import {
  api, ClinicalDocument, Doctor, DOCUMENT_TYPES, DocumentType, errorMessage, formatDateTime, LabResultRow, Page,
  PatientSummary, ReviewedRecord,
} from '../api'
import { useAuth } from '../auth'
import { PatientBanner, PatientPicker, ReasonDialog, StatusChip } from '../components/clinical'
import { FieldGroupEditor, FieldListEditor, ReviewFieldInput } from '../components/ReviewFields'
import { EXTRACTION_CHIP } from './Documents'

const LISTS = {
  diagnoses: { title: 'Diagnoses (as documented)', keys: ['text', 'icd10_code'], add: 'Add diagnosis' },
  allergies: { title: 'Allergies', keys: ['substance', 'reaction'], add: 'Add allergy' },
  vitals: { title: 'Vital signs and observations', keys: ['name', 'value', 'unit'], add: 'Add observation' },
  medications: { title: 'Medicines', keys: ['name', 'dosage', 'route', 'frequency', 'duration', 'quantity', 'instructions'], add: 'Add medicine' },
  lab_results: { title: 'Laboratory results', keys: ['test_name', 'value', 'unit', 'reference_range', 'flag', 'panel_name'], add: 'Add result' },
  immunizations: { title: 'Immunizations', keys: ['vaccine', 'date', 'dose_number', 'lot_number'], add: 'Add vaccine' },
} as const
type ListKey = keyof typeof LISTS

// Sections opened first for each document type.
const FOCUS: Record<DocumentType, string[]> = {
  prescription: ['medications', 'diagnoses'], lab_report: ['lab_results', 'summary'],
  opd_consultation: ['encounter', 'diagnoses', 'vitals', 'medications'], discharge_summary: ['encounter', 'diagnoses', 'medications'],
  immunization: ['immunizations'], wellness: ['vitals'], clinical_note: ['summary', 'encounter'], other: ['summary'],
}

export default function DocumentReview() {
  const { id } = useParams()
  const { can, user } = useAuth()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<ClinicalDocument | null>(null)
  const [record, setRecord] = useState<ReviewedRecord | null>(null)
  const [dirty, setDirty] = useState(false)
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [fileUrl, setFileUrl] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState<ReactNode>('')
  const [dialog, setDialog] = useState<'reject' | 'cancel' | null>(null)

  const load = (d: ClinicalDocument) => {
    setDoc(d)
    setRecord(d.data)
    setDirty(false)
  }

  useEffect(() => {
    api.get<ClinicalDocument>(`/documents/${id}`).then((r) => load(r.data)).catch((e) => setError(errorMessage(e)))
    api.get<Page<Doctor>>('/doctors', { params: { is_active: true, page_size: 100 } }).then((r) => setDoctors(r.data.items)).catch(() => undefined)
    let url = ''
    api.get(`/documents/${id}/file`, { responseType: 'blob' }).then((r) => setFileUrl((url = URL.createObjectURL(r.data))))
    return () => URL.revokeObjectURL(url)
  }, [id])

  // Poll while the AI extraction runs in the background.
  useEffect(() => {
    if (doc?.extraction_status !== 'pending') return
    const timer = setInterval(() => api.get<ClinicalDocument>(`/documents/${id}`).then((r) => r.data.extraction_status !== 'pending' && load(r.data)), 3000)
    return () => clearInterval(timer)
  }, [doc?.extraction_status, id])

  if (!doc) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={400} />

  const editable = can('document:write') && ['draft', 'rejected'].includes(doc.status) && doc.extraction_status !== 'pending'
  const run = async (request: () => Promise<{ data: ClinicalDocument }>, success: string) => {
    try {
      load((await request()).data)
      setMessage(success)
      setError('')
    } catch (e: any) {
      const needs = e?.response?.data?.detail?.requirements as string[] | undefined
      setError(needs ? <>Not ready for approval:<ul>{needs.map((n) => <li key={n}>{n}</li>)}</ul></> : errorMessage(e))
      throw e
    }
  }
  const save = (changes: Record<string, unknown> = {}) =>
    run(() => api.put(`/documents/${id}`, { ...(dirty ? { data: record } : {}), ...changes }), 'Saved.')
  const quiet = (p: Promise<unknown>) => p.catch(() => undefined)
  const update = (changes: Partial<ReviewedRecord>) => {
    setRecord({ ...record!, ...changes })
    setDirty(true)
  }
  const loincAction = (index: number, action: 'confirm' | 'reject', code?: string) => quiet(
    (dirty ? save() : Promise.resolve()).then(() =>
      run(() => api.post(`/documents/${id}/lab-results/${index}/loinc`, { action, code }), action === 'confirm' ? 'LOINC mapping confirmed.' : 'LOINC suggestion rejected.')))

  const section = (key: string, title: string, body: ReactNode) => (
    <Accordion key={key} defaultExpanded={FOCUS[doc.document_type].includes(key)} disableGutters>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography fontWeight={600}>{title}</Typography></AccordionSummary>
      <AccordionDetails>{body}</AccordionDetails>
    </Accordion>
  )

  return (
    <>
      <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" alignItems={{ md: 'center' }} mb={2} spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="h5">{doc.file_name}</Typography>
          <StatusChip status={doc.status} />
          {EXTRACTION_CHIP[doc.extraction_status]}
        </Stack>
        <Stack direction="row" spacing={1}>
          {doc.fhir_record_id && can('fhir:read') && <Button variant="contained" onClick={() => navigate(`/fhir/records/${doc.fhir_record_id}`)}>FHIR preview</Button>}
          {editable && <Button color="error" onClick={() => setDialog('cancel')}>Cancel</Button>}
          {editable && <Button variant="outlined" disabled={!dirty} onClick={() => quiet(save())}>Save</Button>}
          {editable && <Button variant="contained" onClick={() => quiet(save().then(() => run(() => api.post(`/documents/${id}/submit`), 'Submitted for approval.')))}>Submit for approval</Button>}
          {doc.status === 'pending_approval' && can('document:approve') && user?.doctor_id && (
            <>
              <Button color="error" onClick={() => setDialog('reject')}>Return for correction</Button>
              <Button variant="contained" color="success" onClick={() => quiet(run(() => api.post(`/documents/${id}/approve`), 'Approved. FHIR bundle generated.'))}>Approve</Button>
            </>
          )}
        </Stack>
      </Stack>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
      {doc.status_reason && <Alert severity="warning" sx={{ mb: 2 }}>{doc.status === 'rejected' ? 'Returned for correction' : 'Cancelled'}: {doc.status_reason}</Alert>}
      {doc.extraction_status === 'failed' && <Alert severity="warning" sx={{ mb: 2 }}>AI extraction failed ({doc.extraction_error}). Enter the information manually.</Alert>}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 5 }}>
          <Paper sx={{ p: 1, position: { lg: 'sticky' }, top: 80, height: { xs: 480, lg: 'calc(100vh - 110px)' } }}>
            {!fileUrl ? <Skeleton variant="rectangular" height="100%" />
              : doc.content_type === 'application/pdf' ? <iframe title="document" src={fileUrl} style={{ border: 0, width: '100%', height: '100%' }} />
              : doc.content_type.startsWith('image/') ? <Box component="img" src={fileUrl} alt={doc.file_name} sx={{ width: '100%', height: '100%', objectFit: 'contain' }} />
              : <Box component="pre" sx={{ m: 0, p: 1, height: '100%', overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 13 }}>{doc.text}</Box>}
          </Paper>
        </Grid>

        <Grid size={{ xs: 12, lg: 7 }}>
          {doc.extraction_status === 'pending' ? (
            <Paper sx={{ p: 3 }}><Typography mb={2}>The AI is reading the document…</Typography><LinearProgress /></Paper>
          ) : (
            <Stack spacing={2}>
              <Paper sx={{ p: 2 }}>
                <Grid container spacing={2}>
                  <Grid size={{ xs: 12, sm: 6 }}>
                    <TextField select label="Document type" value={doc.document_type} disabled={!editable}
                      onChange={(e) => quiet(save({ document_type: e.target.value }))}>
                      {Object.entries(DOCUMENT_TYPES).map(([k, v]) => <MenuItem key={k} value={k}>{v}</MenuItem>)}
                    </TextField>
                  </Grid>
                  <Grid size={{ xs: 12, sm: 6 }}>
                    <TextField select label="Responsible doctor" value={doc.author_doctor?.id ?? ''} disabled={!editable}
                      onChange={(e) => quiet(save({ author_doctor_id: Number(e.target.value) }))}>
                      {doctors.map((d) => <MenuItem key={d.id} value={d.id}>{d.full_name}</MenuItem>)}
                    </TextField>
                  </Grid>
                  <Grid size={12}>
                    {doc.patient ? (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Box flexGrow={1}><PatientBanner patient={doc.patient} /></Box>
                        {editable && <Button size="small" onClick={() => quiet(save({ patient_id: null }))}>Change</Button>}
                      </Stack>
                    ) : editable ? (
                      <PatientPicker onChange={(p: PatientSummary | null) => p && quiet(save({ patient_id: p.id }))} />
                    ) : <Typography color="text.secondary">No patient linked.</Typography>}
                  </Grid>
                </Grid>
                {doc.warnings.map((w) => <Alert key={w} severity="warning" sx={{ mt: 1 }}>{w}</Alert>)}
                {doc.requirements.length > 0 && editable && (
                  <Alert severity="info" sx={{ mt: 1 }}>Before submitting:<ul style={{ margin: 0 }}>{doc.requirements.map((r) => <li key={r}>{r}</li>)}</ul></Alert>
                )}
                <Stack direction="row" spacing={1} mt={1} flexWrap="wrap">
                  <Chip size="small" label={`${doc.review_counts.ai ?? 0} from AI`} variant="outlined" color="success" />
                  <Chip size="small" label={`${doc.review_counts.manual ?? 0} entered manually`} variant="outlined" color="info" />
                  <Chip size="small" label={`${doc.review_counts.uncertain_pending ?? 0} low-confidence to review`} variant="outlined" color="warning" />
                  {doc.ai_model && <Chip size="small" label={`Model: ${doc.ai_model}`} variant="outlined" />}
                </Stack>
                {doc.approved_by_name && <Typography variant="body2" color="text.secondary" mt={1}>Approved by {doc.approved_by_name} on {formatDateTime(doc.approved_at)}</Typography>}
              </Paper>

              {record && (
                <Box>
                  {section('summary', 'Document date, conclusion and notes', (
                    <Stack spacing={1.5}>
                      <ReviewFieldInput label="Document date" field={record.document_date} readOnly={!editable} onChange={(f) => update({ document_date: f })} />
                      <ReviewFieldInput label="Report conclusion" field={record.report_conclusion} multiline readOnly={!editable} onChange={(f) => update({ report_conclusion: f })} />
                      <ReviewFieldInput label="Notes" field={record.notes} multiline readOnly={!editable} onChange={(f) => update({ notes: f })} />
                    </Stack>
                  ))}
                  {section('patient', 'Patient details on the document', (
                    <>
                      <Typography variant="body2" color="text.secondary" mb={1.5}>Used to check the linked patient. FHIR uses the registered patient record.</Typography>
                      <FieldGroupEditor group={record.patient} readOnly={!editable} onChange={(g) => update({ patient: g })} />
                    </>
                  ))}
                  {section('doctor', 'Doctor details on the document', <FieldGroupEditor group={record.doctor} readOnly={!editable} onChange={(g) => update({ doctor: g })} />)}
                  {section('encounter', 'Visit and clinical notes', (
                    <FieldGroupEditor group={record.encounter} readOnly={!editable} onChange={(g) => update({ encounter: g })}
                      multilineKeys={['chief_complaint', 'history_of_present_illness', 'examination_findings', 'hospital_course', 'treatment_plan', 'follow_up', 'instructions']} />
                  ))}
                  {(Object.keys(LISTS) as ListKey[]).map((key) => section(key, `${LISTS[key].title} (${record[key].length})`, (
                    <FieldListEditor rows={record[key] as LabResultRow[]} keys={[...LISTS[key].keys]} addLabel={LISTS[key].add} readOnly={!editable}
                      onChange={(rows) => update({ [key]: rows })}
                      extra={key === 'lab_results' ? (row, i) => <LoincPanel row={row} editable={editable && can('terminology:confirm')}
                        onAction={(action, code) => loincAction(i, action, code)} /> : undefined} />
                  )))}
                </Box>
              )}
            </Stack>
          )}
        </Grid>
      </Grid>

      {dialog && (
        <ReasonDialog title={dialog === 'reject' ? 'Return document for correction' : 'Cancel document'} onClose={() => setDialog(null)}
          onSubmit={(reason) => run(() => api.post(`/documents/${id}/${dialog}`, { reason }), dialog === 'reject' ? 'Returned for correction.' : 'Cancelled.')} />
      )}
    </>
  )
}

/** LOINC suggestion for one lab result: never applied until an authorised user confirms or corrects it. */
function LoincPanel({ row, editable, onAction }: {
  row: LabResultRow; editable: boolean; onAction: (action: 'confirm' | 'reject', code?: string) => void
}) {
  const [code, setCode] = useState('')
  const m = row.loinc
  if (!m) return null
  const pct = m.confidence != null ? Math.round(m.confidence * 100) : null
  return (
    <Paper variant="outlined" sx={{ p: 1.5, mt: 1, bgcolor: m.status === 'confirmed' ? '#f1f8f1' : '#fbfaf3' }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }} justifyContent="space-between">
        <Box>
          <Typography variant="caption" color="text.secondary">LOINC mapping for “{row.test_name?.value}”</Typography>
          {m.code ? (
            <Typography variant="body2"><b>{m.code}</b> · {m.display}</Typography>
          ) : <Typography variant="body2">{m.status === 'rejected' ? 'Rejected: the test name is used without a code' : 'No suggestion found'}</Typography>}
          {m.matched_term && <Typography variant="caption" color="text.secondary">Matched “{m.matched_term}”{pct != null && ` · confidence ${pct}%`}</Typography>}
          {m.confirmed_by && <Typography variant="caption" display="block">{m.status === 'confirmed' ? 'Confirmed' : 'Rejected'} by {m.confirmed_by}</Typography>}
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" label={m.status} color={m.status === 'confirmed' ? 'success' : m.status === 'suggested' ? (pct === 100 ? 'info' : 'warning') : 'default'} />
          {editable && m.status === 'suggested' && <Button size="small" color="success" onClick={() => onAction('confirm')}>Confirm</Button>}
          {editable && m.status !== 'rejected' && <Button size="small" color="error" onClick={() => onAction('reject')}>Reject</Button>}
        </Stack>
      </Stack>
      {editable && (
        <Stack direction="row" spacing={1} mt={1}>
          <TextField label="Correct LOINC code" placeholder="e.g. 718-7" value={code} onChange={(e) => setCode(e.target.value)} sx={{ maxWidth: 220 }} />
          <Button size="small" disabled={!code.trim()} onClick={() => onAction('confirm', code.trim())}>Use this code</Button>
        </Stack>
      )}
    </Paper>
  )
}

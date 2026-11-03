import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel,
  Grid2 as Grid, IconButton, List, ListItemButton, ListItemText, MenuItem, Paper, Skeleton, Stack, TextField, Typography,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import {
  api, Condition, Encounter, errorMessage, formatDate, formatDateTime, getTerminology, LabOrder, LabTest, Page,
  Prescription, Terminology,
} from '../api'
import { useAuth } from '../auth'
import { FhirButton, PatientBanner, ReasonDialog, StatusChip } from '../components/clinical'

const TEXT_SECTIONS = [
  ['chief_complaint', 'Chief complaint'],
  ['history_present_illness', 'History of present illness'],
  ['clinical_findings', 'Clinical findings / examination'],
  ['treatment_plan', 'Treatment plan'],
  ['follow_up_instructions', 'Follow-up instructions'],
  ['doctor_notes', 'Doctor notes (internal)'],
] as const

type TextKey = (typeof TEXT_SECTIONS)[number][0]

const EMPTY_DIAGNOSIS: Condition = {
  display: '', code: null, clinical_status: 'active', verification_status: 'provisional', onset_date: null, note: null,
}

export default function ConsultationPage() {
  const { id } = useParams()
  const { user, can } = useAuth()
  const navigate = useNavigate()
  const [encounter, setEncounter] = useState<Encounter | null>(null)
  const [text, setText] = useState<Record<TextKey, string>>({} as Record<TextKey, string>)
  const [vitals, setVitals] = useState<Record<string, string>>({})
  const [followUpDate, setFollowUpDate] = useState('')
  const [diagnoses, setDiagnoses] = useState<Condition[]>([])
  const [terms, setTerms] = useState<Terminology | null>(null)
  const [prescriptions, setPrescriptions] = useState<Prescription[]>([])
  const [orders, setOrders] = useState<LabOrder[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState<'cancel' | 'order' | null>(null)

  const load = (e: Encounter) => {
    setEncounter(e)
    setText(Object.fromEntries(TEXT_SECTIONS.map(([k]) => [k, e[k] ?? ''])) as Record<TextKey, string>)
    setVitals(Object.fromEntries(Object.entries(e.vitals ?? {}).map(([k, v]) => [k, v == null ? '' : String(v)])))
    setFollowUpDate(e.follow_up_date ?? '')
    setDiagnoses(e.conditions)
  }
  const loadLinked = () => {
    api.get<Page<Prescription>>('/prescriptions', { params: { encounter_id: id, page_size: 50 } }).then((r) => setPrescriptions(r.data.items))
    if (can('lab:read')) api.get<Page<LabOrder>>('/lab/orders', { params: { encounter_id: id, page_size: 50 } }).then((r) => setOrders(r.data.items))
  }

  useEffect(() => {
    api.get<Encounter>(`/consultations/${id}`).then((r) => load(r.data)).catch((e) => setError(errorMessage(e)))
    getTerminology().then(setTerms)
    loadLinked()
  }, [id])

  if (!encounter || !terms) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={400} />

  const isOwnDoctor = user?.doctor_id === encounter.doctor.id
  const editable = isOwnDoctor && encounter.status === 'draft'
  const active = encounter.status !== 'cancelled'

  const run = async (request: () => Promise<{ data: Encounter }>, success: string) => {
    try {
      load((await request()).data)
      setMessage(success)
      setError('')
    } catch (e) {
      setError(errorMessage(e))
      throw e
    }
  }

  const payload = () => ({
    ...text,
    follow_up_date: followUpDate || null,
    vitals: Object.fromEntries(terms.vitals.map((v) => [v.key, vitals[v.key] ? Number(vitals[v.key]) : null])),
    diagnoses: diagnoses.filter((d) => d.display.trim()).map(({ id: _id, encounter_id: _e, ...d }) => d),
  })
  const save = () => run(() => api.put(`/consultations/${id}`, payload()), 'Draft saved.').catch(() => undefined)
  const finalize = async () => {
    if (!window.confirm('Finalize this consultation? It cannot be edited afterwards.')) return
    try {
      await run(() => api.put(`/consultations/${id}`, payload()), '')
      await run(() => api.post(`/consultations/${id}/finalize`), 'Consultation finalized.')
    } catch { /* error already shown */ }
  }
  const setDiagnosis = (index: number, change: Partial<Condition>) =>
    setDiagnoses(diagnoses.map((d, i) => (i === index ? { ...d, ...change } : d)))

  return (
    <>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} mb={2} spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">Consultation</Typography>
          <StatusChip status={encounter.status} finalLabel="Finalized" />
          {encounter.status === 'approved' && <FhirButton path={`/fhir/consultations/${id}`} source={['consultation', Number(id)]} />}
        </Stack>
        {editable && (
          <Stack direction="row" spacing={1}>
            <Button color="error" onClick={() => setDialog('cancel')}>Cancel visit</Button>
            <Button variant="outlined" onClick={save}>Save draft</Button>
            <Button variant="contained" onClick={finalize}>Finalize</Button>
          </Stack>
        )}
      </Stack>
      <PatientBanner patient={encounter.patient} />
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
      {encounter.status === 'cancelled' && <Alert severity="warning" sx={{ mb: 2 }}>Cancelled: {encounter.status_reason}</Alert>}
      {encounter.status === 'draft' && !isOwnDoctor && (
        <Alert severity="info" sx={{ mb: 2 }}>Only {encounter.doctor.full_name} can edit this consultation.</Alert>
      )}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 8 }}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="body2" color="text.secondary" mb={2}>
              {encounter.doctor.full_name} · visit {formatDateTime(encounter.visit_at)}
              {encounter.approved_at && ` · finalized ${formatDateTime(encounter.approved_at)}`}
            </Typography>

            <Typography variant="subtitle1" fontWeight={600} mb={1}>Vital signs</Typography>
            <Grid container spacing={2} mb={3}>
              {terms.vitals.map((v) => (
                <Grid key={v.key} size={{ xs: 6, sm: 3 }}>
                  <TextField label={`${v.label} (${v.unit === 'Cel' ? '°C' : v.unit})`} type="number" value={vitals[v.key] ?? ''}
                    slotProps={{ input: { readOnly: !editable }, htmlInput: { min: v.min, max: v.max, step: 'any' } }}
                    onChange={(e) => setVitals({ ...vitals, [v.key]: e.target.value })} />
                </Grid>
              ))}
            </Grid>

            <Stack spacing={2}>
              {TEXT_SECTIONS.slice(0, 3).map(([key, label]) => (
                <TextField key={key} label={label} multiline minRows={2} value={text[key]} required={key === 'chief_complaint'}
                  slotProps={{ input: { readOnly: !editable } }} onChange={(e) => setText({ ...text, [key]: e.target.value })} />
              ))}
            </Stack>

            <Divider sx={{ my: 3 }} />
            <Typography variant="subtitle1" fontWeight={600} mb={1}>Diagnosis</Typography>
            <Stack spacing={1.5}>
              {diagnoses.map((d, i) => (
                <Grid container spacing={1} key={d.id ?? `new-${i}`} alignItems="center">
                  <Grid size={{ xs: 12, sm: 5 }}>
                    <TextField label="Diagnosis" value={d.display} slotProps={{ input: { readOnly: !editable } }}
                      onChange={(e) => setDiagnosis(i, { display: e.target.value })} />
                  </Grid>
                  <Grid size={{ xs: 4, sm: 2 }}>
                    <TextField label="ICD-10" value={d.code ?? ''} placeholder="E11.9" slotProps={{ input: { readOnly: !editable } }}
                      onChange={(e) => setDiagnosis(i, { code: e.target.value || null })} />
                  </Grid>
                  <Grid size={{ xs: 4, sm: 2 }}>
                    <TextField select label="Certainty" value={d.verification_status} disabled={!editable}
                      onChange={(e) => setDiagnosis(i, { verification_status: e.target.value as Condition['verification_status'] })}>
                      <MenuItem value="provisional">Provisional</MenuItem>
                      <MenuItem value="confirmed">Confirmed</MenuItem>
                      <MenuItem value="differential">Differential</MenuItem>
                    </TextField>
                  </Grid>
                  <Grid size={{ xs: 4, sm: 2 }}>
                    <ConditionStatus condition={d} editable={editable} canResolve={isOwnDoctor && encounter.status === 'approved'}
                      onChange={(clinical_status) => setDiagnosis(i, { clinical_status })} />
                  </Grid>
                  {editable && (
                    <Grid size={{ xs: 12, sm: 1 }}>
                      <IconButton onClick={() => setDiagnoses(diagnoses.filter((_, j) => j !== i))}><DeleteIcon /></IconButton>
                    </Grid>
                  )}
                </Grid>
              ))}
              {diagnoses.length === 0 && !editable && <Typography color="text.secondary">No diagnosis recorded.</Typography>}
              {editable && (
                <Box><Button startIcon={<AddIcon />} onClick={() => setDiagnoses([...diagnoses, { ...EMPTY_DIAGNOSIS }])}>Add diagnosis</Button></Box>
              )}
            </Stack>

            <Divider sx={{ my: 3 }} />
            <Stack spacing={2}>
              {TEXT_SECTIONS.slice(3).map(([key, label]) => (
                <TextField key={key} label={label} multiline minRows={2} value={text[key]}
                  slotProps={{ input: { readOnly: !editable } }} onChange={(e) => setText({ ...text, [key]: e.target.value })} />
              ))}
              <TextField label="Follow-up date" type="date" value={followUpDate} sx={{ maxWidth: 240 }}
                slotProps={{ input: { readOnly: !editable }, inputLabel: { shrink: true } }} onChange={(e) => setFollowUpDate(e.target.value)} />
            </Stack>
          </Paper>
        </Grid>

        <Grid size={{ xs: 12, lg: 4 }}>
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="h6">Prescriptions</Typography>
                {active && can('prescription:write') && user?.doctor_id && (
                  <Button size="small" startIcon={<AddIcon />} onClick={() => navigate(`/prescriptions/new?encounter=${id}`)}>New</Button>
                )}
              </Stack>
              <List dense>
                {prescriptions.map((p) => (
                  <ListItemButton key={p.id} onClick={() => navigate(`/prescriptions/${p.id}`)}>
                    <ListItemText primary={p.items.map((i) => i.medicine_name).join(', ')} secondary={formatDate(p.prescription_date)} />
                    <StatusChip status={p.status} />
                  </ListItemButton>
                ))}
                {prescriptions.length === 0 && <Typography variant="body2" color="text.secondary">None yet.</Typography>}
              </List>
            </Paper>
            {can('lab:read') && (
              <Paper sx={{ p: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Typography variant="h6">Lab orders</Typography>
                  {active && can('lab:order') && user?.doctor_id && (
                    <Button size="small" startIcon={<AddIcon />} onClick={() => setDialog('order')}>Order tests</Button>
                  )}
                </Stack>
                <List dense>
                  {orders.map((o) => (
                    <ListItemButton key={o.id} onClick={() => navigate(`/lab/orders/${o.id}`)}>
                      <ListItemText primary={o.test.name} secondary={`${o.priority} · ${formatDate(o.created_at)}`} />
                      <StatusChip status={o.status} />
                    </ListItemButton>
                  ))}
                  {orders.length === 0 && <Typography variant="body2" color="text.secondary">None yet.</Typography>}
                </List>
              </Paper>
            )}
          </Stack>
        </Grid>
      </Grid>

      {dialog === 'cancel' && (
        <ReasonDialog title="Cancel consultation" onClose={() => setDialog(null)}
          onSubmit={(reason) => run(() => api.post(`/consultations/${id}/cancel`, { reason }), 'Consultation cancelled.')} />
      )}
      {dialog === 'order' && (
        <OrderTestsDialog encounterId={encounter.id} onClose={() => setDialog(null)}
          onOrdered={() => { setDialog(null); setMessage('Tests ordered.'); loadLinked() }} />
      )}
    </>
  )
}

/** Clinical status of a diagnosis; after finalizing, the doctor can still mark it resolved or active. */
function ConditionStatus({ condition, editable, canResolve, onChange }: {
  condition: Condition; editable: boolean; canResolve: boolean; onChange: (s: Condition['clinical_status']) => void
}) {
  const [error, setError] = useState('')
  const change = async (value: Condition['clinical_status']) => {
    if (!editable) {
      try {
        await api.patch(`/conditions/${condition.id}`, { clinical_status: value })
      } catch (e) {
        return setError(errorMessage(e))
      }
    }
    onChange(value)
  }
  return (
    <TextField select label="Status" value={condition.clinical_status} disabled={!editable && !canResolve}
      error={Boolean(error)} helperText={error} onChange={(e) => change(e.target.value as Condition['clinical_status'])}>
      {['active', 'recurrence', 'inactive', 'resolved'].map((s) => <MenuItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</MenuItem>)}
    </TextField>
  )
}

function OrderTestsDialog({ encounterId, onClose, onOrdered }: { encounterId: number; onClose: () => void; onOrdered: () => void }) {
  const [tests, setTests] = useState<LabTest[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [priority, setPriority] = useState('routine')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<LabTest[]>('/lab/tests', { params: { active_only: true } }).then((r) => setTests(r.data))
  }, [])

  const toggle = (testId: number) => setSelected(selected.includes(testId) ? selected.filter((t) => t !== testId) : [...selected, testId])
  const submit = async () => {
    try {
      await api.post('/lab/orders', { encounter_id: encounterId, test_ids: selected, priority, clinical_notes: notes })
      onOrdered()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Order laboratory tests</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack>
          {tests.map((t) => (
            <FormControlLabel key={t.id} control={<Checkbox checked={selected.includes(t.id)} onChange={() => toggle(t.id)} />}
              label={`${t.name} (${t.sample_type})`} />
          ))}
          {tests.length === 0 && <Typography color="text.secondary">The lab catalogue is empty.</Typography>}
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} mt={2}>
          <TextField select label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)} sx={{ width: { sm: 160 } }}>
            <MenuItem value="routine">Routine</MenuItem>
            <MenuItem value="urgent">Urgent</MenuItem>
            <MenuItem value="stat">STAT</MenuItem>
          </TextField>
          <TextField label="Clinical notes for the lab" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={selected.length === 0} onClick={submit}>Order {selected.length || ''} test{selected.length === 1 ? '' : 's'}</Button>
      </DialogActions>
    </Dialog>
  )
}

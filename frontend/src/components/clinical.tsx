import { useEffect, useState } from 'react'
import { Link as RouterLink, useNavigate } from 'react-router-dom'
import {
  Alert, Autocomplete, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Link, Paper, Stack,
  TextField, Typography,
} from '@mui/material'
import { ageOf, api, errorMessage, GENDERS, Page, PatientSummary } from '../api'
import { useAuth } from '../auth'
import AbhaStatus from './AbhaStatus'

const STATUS: Record<string, { label: string; color: 'default' | 'info' | 'warning' | 'success' | 'error' }> = {
  draft: { label: 'Draft', color: 'default' },
  pending_approval: { label: 'Pending approval', color: 'warning' },
  approved: { label: 'Approved', color: 'success' },
  rejected: { label: 'Rejected', color: 'error' },
  cancelled: { label: 'Cancelled', color: 'default' },
  ordered: { label: 'Ordered', color: 'info' },
  sample_collected: { label: 'Sample collected', color: 'info' },
  in_progress: { label: 'Results in progress', color: 'warning' },
  completed: { label: 'Completed', color: 'success' },
}

/** Status chip for any clinical record; `finalLabel` renames "Approved" (e.g. "Finalized" for consultations). */
export function StatusChip({ status, finalLabel }: { status: string; finalLabel?: string }) {
  const s = STATUS[status] ?? { label: status, color: 'default' as const }
  return <Chip size="small" color={s.color} label={status === 'approved' && finalLabel ? finalLabel : s.label}
    variant={status === 'cancelled' ? 'outlined' : 'filled'} />
}

/** Compact patient header reused on every clinical page so staff never retype patient details. */
export function PatientBanner({ patient }: { patient: PatientSummary }) {
  return (
    <Paper sx={{ p: 2, mb: 2, bgcolor: '#f7fbfd' }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0.5, sm: 3 }} alignItems={{ sm: 'center' }}>
        <Link component={RouterLink} to={`/patients/${patient.id}`} variant="subtitle1" fontWeight={600} underline="hover">
          {patient.full_name}
        </Link>
        <Typography variant="body2">{patient.patient_code}</Typography>
        <Typography variant="body2">{ageOf(patient)} · {GENDERS[patient.gender as keyof typeof GENDERS]}</Typography>
        <Typography variant="body2">{patient.mobile}</Typography>
        <AbhaStatus patient={patient} />
      </Stack>
    </Paper>
  )
}

/** Ask for a reason before rejecting or cancelling a record. */
export function ReasonDialog({ title, onClose, onSubmit }: {
  title: string; onClose: () => void; onSubmit: (reason: string) => Promise<unknown>
}) {
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const submit = async () => {
    try {
      await onSubmit(reason.trim())
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <TextField label="Reason" multiline minRows={2} value={reason} onChange={(e) => setReason(e.target.value)} sx={{ mt: 1 }} autoFocus />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Back</Button>
        <Button variant="contained" color="error" disabled={reason.trim().length < 3} onClick={submit}>Confirm</Button>
      </DialogActions>
    </Dialog>
  )
}

/** Search-as-you-type patient selector backed by the existing patient search API. */
export function PatientPicker({ onChange }: { onChange: (p: PatientSummary | null) => void }) {
  const [input, setInput] = useState('')
  const [options, setOptions] = useState<PatientSummary[]>([])

  useEffect(() => {
    const timer = setTimeout(() => {
      api.get<Page<PatientSummary>>('/patients', { params: { q: input || undefined, page_size: 10 } })
        .then((r) => setOptions(r.data.items))
    }, 250)
    return () => clearTimeout(timer)
  }, [input])

  return (
    <Autocomplete
      options={options}
      filterOptions={(x) => x}
      getOptionLabel={(p) => `${p.full_name} · ${p.patient_code} · ${p.mobile}`}
      onInputChange={(_, value) => setInput(value)}
      onChange={(_, value) => onChange(value)}
      renderInput={(params) => <TextField {...params} label="Patient (name, ID or mobile)" />}
    />
  )
}

/** FHIR document of an approved record: opens the stored record (validation, download) for users who may
 * generate one, otherwise shows the JSON. `source` is e.g. ["prescription", 4]. */
export function FhirButton({ path, source }: { path: string; source: [string, number] }) {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [json, setJson] = useState<string | null>(null)
  const [error, setError] = useState('')
  const open = () => (can('fhir:generate')
    ? api.post<{ id: number }>('/fhir/records', { source_type: source[0], source_id: source[1] }).then((r) => navigate(`/fhir/records/${r.data.id}`))
    : api.get(path).then((r) => setJson(JSON.stringify(r.data, null, 2)))).catch((e) => setError(errorMessage(e)))
  return (
    <>
      <Button size="small" onClick={open}>View FHIR</Button>
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      <Dialog open={json !== null} onClose={() => setJson(null)} maxWidth="md" fullWidth>
        <DialogTitle>FHIR R4 document bundle</DialogTitle>
        <DialogContent>
          <Alert severity="info" sx={{ mb: 2 }}>
            Generated for the ABDM IG ndhm.in 6.5.0. Validation runs from the FHIR Records page (requires FHIR permissions).
          </Alert>
          <Box component="pre" sx={{ fontSize: 12, bgcolor: '#f4f6f9', p: 2, borderRadius: 1, overflow: 'auto', maxHeight: '60vh' }}>{json}</Box>
        </DialogContent>
        <DialogActions><Button onClick={() => setJson(null)}>Close</Button></DialogActions>
      </Dialog>
    </>
  )
}

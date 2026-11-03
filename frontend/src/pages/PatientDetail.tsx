import { ReactNode, useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Grid2 as Grid, List, ListItem,
  ListItemText, MenuItem, Paper, Skeleton, Stack, TextField, Typography,
} from '@mui/material'
import EditIcon from '@mui/icons-material/Edit'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { ageOf, api, AuditLog, errorMessage, formatAbha, formatDate, formatDateTime, GENDERS, Page, Patient } from '../api'
import { useAuth } from '../auth'
import AbhaStatus from '../components/AbhaStatus'
import PatientForm, { PatientPayload } from '../components/PatientForm'
import { startConsultation } from './Consultations'
import { Briefing } from './Assistant'

const VERIFY_METHODS = {
  physical_abha_card: 'Physical ABHA card checked',
  abha_app_profile: 'ABHA app profile shown by patient',
}

function Info({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Grid size={{ xs: 12, sm: 6, md: 4 }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography>{value || '—'}</Typography>
    </Grid>
  )
}

export default function PatientDetail() {
  const { id } = useParams()
  const { can, user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [patient, setPatient] = useState<Patient | null>(null)
  const [history, setHistory] = useState<AuditLog[]>([])
  const [error, setError] = useState('')
  const [message, setMessage] = useState(location.state?.created ? 'Patient registered successfully.' : '')
  const [editing, setEditing] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [busy, setBusy] = useState(false)

  const loadHistory = () => {
    if (can('audit:read')) {
      api.get<Page<AuditLog>>('/audit-logs', { params: { entity_type: 'patient', entity_id: id, page_size: 20 } })
        .then((r) => setHistory(r.data.items))
    }
  }

  useEffect(() => {
    api.get<Patient>(`/patients/${id}`).then((r) => setPatient(r.data)).catch((e) => setError(errorMessage(e)))
    loadHistory()
  }, [id])

  const run = async (action: () => Promise<{ data: Patient }>, success: string) => {
    setBusy(true)
    try {
      setPatient((await action()).data)
      setMessage(success)
      setError('')
      setEditing(false)
      setVerifying(false)
      loadHistory()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const update = (payload: PatientPayload) => run(() => api.patch(`/patients/${id}`, payload), 'Patient details updated.')

  if (!patient) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />

  const hasAbha = Boolean(patient.abha_number || patient.abha_address)

  return (
    <>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} mb={2} spacing={1}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/patients')}>Patients</Button>
          <Typography variant="h5">{patient.full_name}</Typography>
          <AbhaStatus patient={patient} />
        </Stack>
        <Stack direction="row" spacing={1}>
          {can('clinical:read') && <Button variant="outlined" onClick={() => navigate(`/patients/${id}/history`)}>Medical history</Button>}
          {can('consultation:write') && user?.doctor_id && (
            <Button variant="outlined" onClick={() => startConsultation(patient.id, navigate).catch((e) => setError(errorMessage(e)))}>
              Start consultation
            </Button>
          )}
          {can('patient:update') && (
            <Button variant="contained" startIcon={<EditIcon />} onClick={() => setEditing(true)}>Edit details</Button>
          )}
        </Stack>
      </Stack>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 8 }}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" mb={2}>Patient details</Typography>
            <Grid container spacing={2}>
              <Info label="Patient ID" value={patient.patient_code} />
              <Info label="Gender" value={GENDERS[patient.gender]} />
              <Info label="Age" value={ageOf(patient)} />
              <Info label="Date of birth" value={patient.date_of_birth ? formatDate(patient.date_of_birth) : `Unknown (year ~${patient.year_of_birth})`} />
              <Info label="Mobile" value={patient.mobile} />
              <Info label="Registered" value={`${formatDateTime(patient.created_at)}${patient.registered_by_name ? ` by ${patient.registered_by_name}` : ''}`} />
              <Info label="Address" value={[patient.address_line, patient.city, patient.state, patient.pincode].filter(Boolean).join(', ')} />
              <Info label="Emergency contact" value={patient.emergency_contact_name && `${patient.emergency_contact_name}${patient.emergency_contact_relation ? ` (${patient.emergency_contact_relation})` : ''}`} />
              <Info label="Emergency phone" value={patient.emergency_contact_phone} />
            </Grid>
          </Paper>
        </Grid>

        <Grid size={{ xs: 12, lg: 4 }}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" mb={2}>ABHA</Typography>
            <Stack spacing={1.5}>
              <Box>
                <Typography variant="caption" color="text.secondary">ABHA number</Typography>
                <Typography>{formatAbha(patient.abha_number) || '—'}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">ABHA address</Typography>
                <Typography>{patient.abha_address || '—'}</Typography>
              </Box>
              {patient.abha_verified ? (
                <Alert severity="success">
                  Verified {formatDateTime(patient.abha_verified_at)} by {patient.abha_verified_by_name}
                  <br />Method: {VERIFY_METHODS[patient.abha_verification_method as keyof typeof VERIFY_METHODS] ?? patient.abha_verification_method}
                </Alert>
              ) : hasAbha ? (
                <Alert severity="warning">Not verified. Do not rely on these identifiers until verified.</Alert>
              ) : null}
              {can('abha:verify') && hasAbha && (
                patient.abha_verified
                  ? <Button color="warning" disabled={busy} onClick={() => run(() => api.delete(`/patients/${id}/abha/verify`), 'ABHA verification revoked.')}>Revoke verification</Button>
                  : <Button variant="outlined" onClick={() => setVerifying(true)}>Record verification</Button>
              )}
            </Stack>
          </Paper>
        </Grid>

        {can('clinical:read') && <Grid size={12}><Briefing patientId={patient.id} /></Grid>}
        {can('audit:read') && (
          <Grid size={12}>
            <Paper sx={{ p: 3 }}>
              <Typography variant="h6" mb={1}>Record history</Typography>
              <List dense disablePadding>
                {history.map((h) => (
                  <ListItem key={h.id} disableGutters divider>
                    <ListItemText primary={`${h.action} — ${h.user_name ?? 'Unknown user'}`} secondary={formatDateTime(h.created_at)} />
                  </ListItem>
                ))}
              </List>
            </Paper>
          </Grid>
        )}
      </Grid>

      <Dialog open={editing} onClose={() => setEditing(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit patient</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <Box pt={1}>
            <PatientForm patient={patient} submitLabel="Save changes" busy={busy} onSubmit={update} onCancel={() => setEditing(false)} />
          </Box>
        </DialogContent>
      </Dialog>

      {verifying && (
        <VerifyDialog busy={busy} onClose={() => setVerifying(false)}
          onSubmit={(body) => run(() => api.post(`/patients/${id}/abha/verify`, body), 'ABHA verification recorded.')} />
      )}
    </>
  )
}

function VerifyDialog({ busy, onClose, onSubmit }: { busy: boolean; onClose: () => void; onSubmit: (body: { method: string; note: string }) => void }) {
  const [method, setMethod] = useState('physical_abha_card')
  const [note, setNote] = useState('')
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Record ABHA verification</DialogTitle>
      <DialogContent>
        <Stack spacing={2} pt={1}>
          <Alert severity="info">
            Only record verification after confirming the ABHA number/address belongs to this patient.
            Verification through ABDM services will be added in a later phase.
          </Alert>
          <TextField select label="How was it verified?" value={method} onChange={(e) => setMethod(e.target.value)}>
            {Object.entries(VERIFY_METHODS).map(([k, v]) => <MenuItem key={k} value={k}>{v}</MenuItem>)}
          </TextField>
          <TextField label="Note (what was checked)" multiline minRows={2} value={note} onChange={(e) => setNote(e.target.value)}
            helperText="At least 5 characters" />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy || note.trim().length < 5} onClick={() => onSubmit({ method, note: note.trim() })}>
          Mark as verified
        </Button>
      </DialogActions>
    </Dialog>
  )
}

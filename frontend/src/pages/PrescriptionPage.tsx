import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Alert, Box, Button, Checkbox, FormControlLabel, Grid2 as Grid, IconButton, MenuItem, Paper, Skeleton, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import PrintIcon from '@mui/icons-material/Print'
import { api, Encounter, errorMessage, formatDate, formatDateTime, getTerminology, Prescription, PrescriptionItem, Terminology } from '../api'
import { useAuth } from '../auth'
import { FhirButton, PatientBanner, ReasonDialog, StatusChip } from '../components/clinical'

const EMPTY_ITEM: PrescriptionItem = {
  medicine_name: '', dosage: '', route: 'oral', frequency: 'BD', duration_days: 5, quantity: null, instructions: null,
}

export default function PrescriptionPage() {
  const { id } = useParams()
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isNew = id === 'new'
  const [prescription, setPrescription] = useState<Prescription | null>(null)
  const [encounter, setEncounter] = useState<Encounter | null>(null)
  const [items, setItems] = useState<PrescriptionItem[]>([{ ...EMPTY_ITEM }])
  const [notes, setNotes] = useState('')
  const [terms, setTerms] = useState<Terminology | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [dialog, setDialog] = useState<'reject' | 'cancel' | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = (p: Prescription) => {
    setPrescription(p)
    setItems(p.items)
    setNotes(p.notes ?? '')
    setReviewed(false)
  }

  useEffect(() => {
    getTerminology().then(setTerms)
    if (isNew) {
      api.get<Encounter>(`/consultations/${search.get('encounter')}`).then((r) => setEncounter(r.data)).catch((e) => setError(errorMessage(e)))
    } else {
      api.get<Prescription>(`/prescriptions/${id}`).then((r) => load(r.data)).catch((e) => setError(errorMessage(e)))
    }
  }, [id])

  const patient = prescription?.patient ?? encounter?.patient
  if (!terms || !patient) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />

  const status = prescription?.status ?? 'draft'
  const isPrescriber = isNew || user?.doctor_id === prescription?.doctor.id
  const editable = isPrescriber && (status === 'draft' || status === 'rejected')
  const routeLabel = (key: string) => terms.routes.find((r) => r.key === key)?.label ?? key
  const frequencyLabel = (key: string) => terms.frequencies.find((f) => f.key === key)?.label ?? key

  const act = async (request: () => Promise<{ data: Prescription }>, success: string) => {
    try {
      load((await request()).data)
      setMessage(success)
      setError('')
    } catch (e) {
      setError(errorMessage(e))
      throw e
    }
  }
  const body = () => ({
    items: items.map((i) => ({ ...i, quantity: i.quantity || null, instructions: i.instructions || null })),
    notes: notes || null,
  })
  const save = async () => {
    try {
      if (isNew) {
        const { data } = await api.post<Prescription>('/prescriptions', { ...body(), encounter_id: encounter!.id })
        navigate(`/prescriptions/${data.id}`, { replace: true })
      } else {
        await act(() => api.put(`/prescriptions/${id}`, body()), 'Draft saved.')
      }
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const submit = () => act(() => api.put(`/prescriptions/${id}`, body()), '')
    .then(() => act(() => api.post(`/prescriptions/${id}/submit`), 'Submitted. Review the prescription below and approve it.'))
    .catch(() => undefined)
  const setItem = (index: number, change: Partial<PrescriptionItem>) => setItems(items.map((it, i) => (i === index ? { ...it, ...change } : it)))

  return (
    <>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} mb={2} spacing={1} className="no-print">
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">{isNew ? 'New prescription' : `Prescription #${id}`}</Typography>
          <StatusChip status={status} />
          {status === 'approved' && <FhirButton path={`/fhir/prescriptions/${id}`} source={['prescription', Number(id)]} />}
        </Stack>
        <Stack direction="row" spacing={1}>
          {prescription && <Button onClick={() => navigate(`/consultations/${prescription.encounter_id}`)}>Open consultation</Button>}
          {status === 'approved' && <Button startIcon={<PrintIcon />} onClick={() => window.print()}>Print</Button>}
          {isPrescriber && prescription && status !== 'cancelled' && <Button color="error" onClick={() => setDialog('cancel')}>Cancel</Button>}
        </Stack>
      </Stack>
      <Box className="no-print">
        {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
        {prescription?.status_reason && <Alert severity="warning" sx={{ mb: 2 }}>{status === 'rejected' ? 'Rejected' : 'Cancelled'}: {prescription.status_reason}</Alert>}
      </Box>

      {editable ? (
        <>
          <PatientBanner patient={patient} />
          <Paper sx={{ p: 3 }}>
            <Stack spacing={2}>
              {items.map((item, i) => (
                <Grid container spacing={1} key={i} alignItems="center">
                  <Grid size={{ xs: 12, md: 3 }}>
                    <TextField label="Medicine" value={item.medicine_name} required onChange={(e) => setItem(i, { medicine_name: e.target.value })} />
                  </Grid>
                  <Grid size={{ xs: 6, md: 1.5 }}>
                    <TextField label="Dosage" placeholder="1 tablet" value={item.dosage} required onChange={(e) => setItem(i, { dosage: e.target.value })} />
                  </Grid>
                  <Grid size={{ xs: 6, md: 1.5 }}>
                    <TextField select label="Route" value={item.route} onChange={(e) => setItem(i, { route: e.target.value })}>
                      {terms.routes.map((r) => <MenuItem key={r.key} value={r.key}>{r.label}</MenuItem>)}
                    </TextField>
                  </Grid>
                  <Grid size={{ xs: 6, md: 1.5 }}>
                    <TextField select label="Frequency" value={item.frequency} onChange={(e) => setItem(i, { frequency: e.target.value })}>
                      {terms.frequencies.map((f) => <MenuItem key={f.key} value={f.key}>{f.key} – {f.label}</MenuItem>)}
                    </TextField>
                  </Grid>
                  <Grid size={{ xs: 3, md: 1 }}>
                    <TextField label="Days" type="number" value={item.duration_days} onChange={(e) => setItem(i, { duration_days: Number(e.target.value) })} />
                  </Grid>
                  <Grid size={{ xs: 3, md: 1 }}>
                    <TextField label="Qty" type="number" value={item.quantity ?? ''} onChange={(e) => setItem(i, { quantity: e.target.value ? Number(e.target.value) : null })} />
                  </Grid>
                  <Grid size={{ xs: 10, md: 2 }}>
                    <TextField label="Instructions" placeholder="After food" value={item.instructions ?? ''} onChange={(e) => setItem(i, { instructions: e.target.value })} />
                  </Grid>
                  <Grid size={{ xs: 2, md: 0.5 }}>
                    <IconButton disabled={items.length === 1} onClick={() => setItems(items.filter((_, j) => j !== i))}><DeleteIcon /></IconButton>
                  </Grid>
                </Grid>
              ))}
              <Box><Button startIcon={<AddIcon />} onClick={() => setItems([...items, { ...EMPTY_ITEM }])}>Add medicine</Button></Box>
              <TextField label="Notes / advice" multiline minRows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button variant="outlined" onClick={save}>Save draft</Button>
                {!isNew && <Button variant="contained" onClick={submit}>Submit for approval</Button>}
              </Stack>
            </Stack>
          </Paper>
        </>
      ) : prescription && (
        <Paper sx={{ p: 3 }}>
          <Stack direction="row" justifyContent="space-between" mb={2}>
            <Box>
              <Typography variant="h6">{user?.hospital_name}</Typography>
              <Typography>{prescription.doctor.full_name}{prescription.doctor.qualification && `, ${prescription.doctor.qualification}`}</Typography>
              <Typography variant="body2" color="text.secondary">
                Reg. no. {prescription.doctor.registration_number}{prescription.doctor.specialization && ` · ${prescription.doctor.specialization}`}
              </Typography>
            </Box>
            <Box textAlign="right">
              <Typography variant="body2">Date: {formatDate(prescription.prescription_date)}</Typography>
              <Typography variant="body2">Consultation #{prescription.encounter_id}</Typography>
            </Box>
          </Stack>
          <PatientBanner patient={patient} />
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell>
                <TableCell>Medicine</TableCell>
                <TableCell>Dosage</TableCell>
                <TableCell>Route</TableCell>
                <TableCell>Frequency</TableCell>
                <TableCell>Duration</TableCell>
                <TableCell>Qty</TableCell>
                <TableCell>Instructions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {prescription.items.map((item, i) => (
                <TableRow key={i}>
                  <TableCell>{i + 1}</TableCell>
                  <TableCell>{item.medicine_name}</TableCell>
                  <TableCell>{item.dosage}</TableCell>
                  <TableCell>{routeLabel(item.route)}</TableCell>
                  <TableCell>{item.frequency} ({frequencyLabel(item.frequency)})</TableCell>
                  <TableCell>{item.duration_days} days</TableCell>
                  <TableCell>{item.quantity}</TableCell>
                  <TableCell>{item.instructions}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {prescription.notes && <Typography mt={2}><b>Advice:</b> {prescription.notes}</Typography>}
          <Typography variant="body2" color="text.secondary" mt={2}>
            {status === 'approved'
              ? `Approved by ${prescription.approved_by_name} on ${formatDateTime(prescription.approved_at)}`
              : `Created by ${prescription.created_by_name} on ${formatDateTime(prescription.created_at)}`}
          </Typography>

          {status === 'pending_approval' && isPrescriber && (
            <Paper sx={{ p: 2, mt: 3, bgcolor: '#fffbea' }} className="no-print">
              <FormControlLabel control={<Checkbox checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />}
                label="I have reviewed every medicine, dose, frequency and duration for this patient." />
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button color="error" onClick={() => setDialog('reject')}>Reject and revise</Button>
                <Button variant="contained" disabled={!reviewed}
                  onClick={() => act(() => api.post(`/prescriptions/${id}/approve`), 'Prescription approved.').catch(() => undefined)}>
                  Approve prescription
                </Button>
              </Stack>
            </Paper>
          )}
        </Paper>
      )}

      {dialog && (
        <ReasonDialog title={dialog === 'reject' ? 'Reject prescription' : 'Cancel prescription'} onClose={() => setDialog(null)}
          onSubmit={(reason) => act(() => api.post(`/prescriptions/${id}/${dialog}`, { reason }),
            dialog === 'reject' ? 'Rejected. Edit the prescription and submit again.' : 'Prescription cancelled.')} />
      )}
    </>
  )
}

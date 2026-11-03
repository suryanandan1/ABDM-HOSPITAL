import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { api, ageOf, DuplicateCandidate, errorMessage, GENDERS, Patient } from '../api'
import PatientForm, { PatientPayload } from '../components/PatientForm'

const LEVEL_COLOR = { exact: 'error', likely: 'warning', possible: 'info' } as const

export default function PatientRegister() {
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PatientPayload | null>(null)
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([])

  const save = async (payload: PatientPayload, confirmNotDuplicate = false) => {
    setBusy(true)
    setError('')
    try {
      const { data } = await api.post<Patient>('/patients', { ...payload, confirm_not_duplicate: confirmNotDuplicate })
      navigate(`/patients/${data.id}`, { state: { created: true } })
    } catch (e) {
      const detail = axios.isAxiosError(e) ? e.response?.data?.detail : null
      if (detail?.duplicates) {
        setPending(payload)
        setDuplicates(detail.duplicates)
      } else {
        setError(errorMessage(e))
      }
    } finally {
      setBusy(false)
    }
  }

  const hasExact = duplicates.some((d) => d.level === 'exact')
  const closeDialog = () => setDuplicates([])

  return (
    <>
      <Typography variant="h5" mb={2}>Register patient</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper sx={{ p: 3 }}>
        <PatientForm submitLabel="Register patient" busy={busy} onSubmit={(p) => save(p)} onCancel={() => navigate('/patients')} />
      </Paper>

      <Dialog open={duplicates.length > 0} onClose={closeDialog} maxWidth="md" fullWidth>
        <DialogTitle>{hasExact ? 'Patient already registered' : 'Possible duplicate patients'}</DialogTitle>
        <DialogContent>
          <Alert severity={hasExact ? 'error' : 'warning'} sx={{ mb: 2 }}>
            {hasExact
              ? 'A patient with the same ABHA identifier already exists. Open the existing record instead of registering again.'
              : 'These existing patients look similar. Check with the patient before creating a new record.'}
          </Alert>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Match</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Age / Gender</TableCell>
                <TableCell>Mobile</TableCell>
                <TableCell>Reasons</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {duplicates.map(({ patient, level, reasons }) => (
                <TableRow key={patient.id}>
                  <TableCell><Chip size="small" label={level} color={LEVEL_COLOR[level]} /></TableCell>
                  <TableCell>{patient.full_name}<br /><Typography variant="caption">{patient.patient_code}</Typography></TableCell>
                  <TableCell>{ageOf(patient)} / {GENDERS[patient.gender]}</TableCell>
                  <TableCell>{patient.mobile}</TableCell>
                  <TableCell>{reasons.join(', ')}</TableCell>
                  <TableCell>
                    <Button size="small" onClick={() => navigate(`/patients/${patient.id}`)}>Open</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DialogContent>
        <DialogActions>
          <Stack direction="row" spacing={1}>
            <Button onClick={closeDialog}>Back to form</Button>
            {!hasExact && pending && (
              <Button color="warning" variant="contained" disabled={busy}
                onClick={() => { closeDialog(); save(pending, true) }}>
                This is a different person — register
              </Button>
            )}
          </Stack>
        </DialogActions>
      </Dialog>
    </>
  )
}

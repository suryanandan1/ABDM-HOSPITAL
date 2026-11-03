import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, LinearProgress, MenuItem,
  Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { api, Encounter, errorMessage, formatDateTime, PatientSummary } from '../api'
import { useAuth } from '../auth'
import { PatientPicker, StatusChip } from '../components/clinical'
import { usePaged } from '../usePaged'

/** Start a draft consultation for a patient and open it. Shared by this page and the patient profile. */
export async function startConsultation(patientId: number, navigate: (to: string) => void) {
  const { data } = await api.post<Encounter>('/consultations', { patient_id: patientId })
  navigate(`/consultations/${data.id}`)
}

export default function Consultations() {
  const { can, user } = useAuth()
  const navigate = useNavigate()
  const isDoctor = user?.role_code === 'doctor'
  const [status, setStatus] = useState('')
  const [mine, setMine] = useState(isDoctor)
  const [creating, setCreating] = useState(false)
  const { data, loading, error, pagination } = usePaged<Encounter>('/consultations', { status, mine: mine || null })

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Consultations</Typography>
        {can('consultation:write') && (
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreating(true)}>New consultation</Button>
        )}
      </Stack>
      <Paper>
        <Stack direction="row" spacing={2} sx={{ p: 2 }} alignItems="center">
          <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 200 }}>
            <MenuItem value="">All</MenuItem>
            <MenuItem value="draft">Draft</MenuItem>
            <MenuItem value="approved">Finalized</MenuItem>
            <MenuItem value="cancelled">Cancelled</MenuItem>
          </TextField>
          {isDoctor && <FormControlLabel control={<Switch checked={mine} onChange={(e) => setMine(e.target.checked)} />} label="My patients" />}
        </Stack>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Visit</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Doctor</TableCell>
                <TableCell>Chief complaint</TableCell>
                <TableCell>Diagnosis</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((e) => (
                <TableRow key={e.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/consultations/${e.id}`)}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(e.visit_at)}</TableCell>
                  <TableCell>{e.patient.full_name}<br /><Typography variant="caption">{e.patient.patient_code}</Typography></TableCell>
                  <TableCell>{e.doctor.full_name}</TableCell>
                  <TableCell sx={{ maxWidth: 260 }}>{e.chief_complaint}</TableCell>
                  <TableCell>{e.conditions.map((c) => c.display).join(', ')}</TableCell>
                  <TableCell><StatusChip status={e.status} finalLabel="Finalized" /></TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No consultations found.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>
      {creating && <NewConsultationDialog onClose={() => setCreating(false)} />}
    </>
  )
}

function NewConsultationDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [patient, setPatient] = useState<PatientSummary | null>(null)
  const [error, setError] = useState('')
  const start = () => patient && startConsultation(patient.id, navigate).catch((e) => setError(errorMessage(e)))
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New consultation</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Box pt={1}><PatientPicker onChange={setPatient} /></Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!patient} onClick={start}>Start consultation</Button>
      </DialogActions>
    </Dialog>
  )
}

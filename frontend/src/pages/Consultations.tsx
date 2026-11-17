import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Box, Button, FormControlLabel, MenuItem, Paper, Stack, Switch, TextField } from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { api, Encounter, errorMessage, formatDateTime, PatientSummary } from '../api'
import { useAuth } from '../auth'
import { PatientPicker, StatusChip } from '../components/clinical'
import { FormDialog, PagedTable, PageHeader, PatientCell } from '../components/ui'
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
  const paged = usePaged<Encounter>('/consultations', { status, mine: mine || null })

  return (
    <>
      <PageHeader title="Consultations">
        {can('consultation:write') && (
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreating(true)}>New consultation</Button>
        )}
      </PageHeader>
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
        <PagedTable paged={paged} empty="No consultations found." onRowClick={(e) => navigate(`/consultations/${e.id}`)} columns={[
          ['Visit', (e) => formatDateTime(e.visit_at), { whiteSpace: 'nowrap' }],
          ['Patient', (e) => <PatientCell patient={e.patient} />],
          ['Doctor', (e) => e.doctor.full_name],
          ['Chief complaint', (e) => e.chief_complaint, { maxWidth: 260 }],
          ['Diagnosis', (e) => e.conditions.map((c) => c.display).join(', ')],
          ['Status', (e) => <StatusChip status={e.status} finalLabel="Finalized" />],
        ]} />
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
    <FormDialog title="New consultation" onClose={onClose}
      actions={<Button variant="contained" disabled={!patient} onClick={start}>Start consultation</Button>}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box pt={1}><PatientPicker onChange={setPatient} /></Box>
    </FormDialog>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { formatDate, Prescription } from '../api'
import { StatusChip } from '../components/clinical'
import { PagedTable, PatientCell } from '../components/ui'
import { usePaged } from '../usePaged'

export default function Prescriptions() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const paged = usePaged<Prescription>('/prescriptions', { status })

  return (
    <>
      <Typography variant="h5" mb={2}>Prescription history</Typography>
      <Paper>
        <Stack direction="row" sx={{ p: 2 }}>
          <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 220 }}>
            <MenuItem value="">All</MenuItem>
            {['draft', 'pending_approval', 'approved', 'rejected', 'cancelled'].map((s) => (
              <MenuItem key={s} value={s}>{s.replace('_', ' ')}</MenuItem>
            ))}
          </TextField>
        </Stack>
        <PagedTable paged={paged} empty="No prescriptions found." onRowClick={(p) => navigate(`/prescriptions/${p.id}`)} columns={[
          ['#', (p) => p.id],
          ['Date', (p) => formatDate(p.prescription_date)],
          ['Patient', (p) => <PatientCell patient={p.patient} />],
          ['Doctor', (p) => p.doctor.full_name],
          ['Medicines', (p) => p.items.map((i) => i.medicine_name).join(', ')],
          ['Status', (p) => <StatusChip status={p.status} />],
        ]} />
      </Paper>
    </>
  )
}

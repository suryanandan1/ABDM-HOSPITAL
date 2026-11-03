import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, LinearProgress, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TablePagination,
  TableRow, TextField, Typography,
} from '@mui/material'
import { formatDate, Prescription } from '../api'
import { StatusChip } from '../components/clinical'
import { usePaged } from '../usePaged'

export default function Prescriptions() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const { data, loading, error, pagination } = usePaged<Prescription>('/prescriptions', { status })

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
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell>
                <TableCell>Date</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Doctor</TableCell>
                <TableCell>Medicines</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((p) => (
                <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/prescriptions/${p.id}`)}>
                  <TableCell>{p.id}</TableCell>
                  <TableCell>{formatDate(p.prescription_date)}</TableCell>
                  <TableCell>{p.patient.full_name}<br /><Typography variant="caption">{p.patient.patient_code}</Typography></TableCell>
                  <TableCell>{p.doctor.full_name}</TableCell>
                  <TableCell>{p.items.map((i) => i.medicine_name).join(', ')}</TableCell>
                  <TableCell><StatusChip status={p.status} /></TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No prescriptions found.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>
    </>
  )
}

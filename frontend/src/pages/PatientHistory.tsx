import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Chip, Grid2 as Grid, List, ListItemButton, ListItemText, Paper, Skeleton, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { api, errorMessage, formatDate, formatDateTime, getTerminology, PatientHistory as History, Terminology } from '../api'
import { PatientBanner } from '../components/clinical'

export default function PatientHistory() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [history, setHistory] = useState<History | null>(null)
  const [terms, setTerms] = useState<Terminology | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    getTerminology().then(setTerms)
    api.get<History>(`/patients/${id}/history`).then((r) => setHistory(r.data)).catch((e) => setError(errorMessage(e)))
  }, [id])

  if (!history) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />

  const latestVitals = history.consultations.find((c) => c.vitals && Object.values(c.vitals).some((v) => v != null))

  return (
    <>
      <Typography variant="h5" mb={2}>Medical history</Typography>
      <PatientBanner patient={history.patient} />
      <Typography variant="body2" color="text.secondary" mb={2}>
        Shows finalized consultations, approved prescriptions and approved laboratory reports only.
      </Typography>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 4 }}>
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Active conditions</Typography>
              <Stack direction="row" flexWrap="wrap" gap={1}>
                {history.active_conditions.map((c) => (
                  <Chip key={c.id} label={`${c.display}${c.code ? ` (${c.code})` : ''}`} color={c.verification_status === 'confirmed' ? 'primary' : 'default'} />
                ))}
                {history.active_conditions.length === 0 && <Typography color="text.secondary">None recorded.</Typography>}
              </Stack>
            </Paper>
            {latestVitals && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="h6">Latest vitals</Typography>
                <Typography variant="caption" color="text.secondary">{formatDateTime(latestVitals.visit_at)}</Typography>
                {terms?.vitals.filter((t) => latestVitals.vitals![t.key] != null).map((t) => (
                  <Typography key={t.key} variant="body2">{t.label}: {latestVitals.vitals![t.key]} {t.unit === 'Cel' ? '°C' : t.unit}</Typography>
                ))}
              </Paper>
            )}
          </Stack>
        </Grid>

        <Grid size={{ xs: 12, md: 8 }}>
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6">Consultations</Typography>
              <List dense>
                {history.consultations.map((c) => (
                  <ListItemButton key={c.id} onClick={() => navigate(`/consultations/${c.id}`)}>
                    <ListItemText
                      primary={`${formatDate(c.visit_at)} · ${c.doctor.full_name} — ${c.chief_complaint ?? ''}`}
                      secondary={[c.conditions.map((d) => d.display).join(', '), c.treatment_plan].filter(Boolean).join(' · ')}
                    />
                  </ListItemButton>
                ))}
                {history.consultations.length === 0 && <Typography color="text.secondary">No finalized consultations.</Typography>}
              </List>
            </Paper>

            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Prescriptions</Typography>
              <Box sx={{ overflowX: 'auto' }}>
                <Table size="small">
                  <TableHead>
                    <TableRow><TableCell>Date</TableCell><TableCell>Medicine</TableCell><TableCell>Dose</TableCell><TableCell>Duration</TableCell><TableCell>Doctor</TableCell></TableRow>
                  </TableHead>
                  <TableBody>
                    {history.prescriptions.flatMap((p) => p.items.map((i, n) => (
                      <TableRow key={`${p.id}-${n}`} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/prescriptions/${p.id}`)}>
                        <TableCell>{formatDate(p.prescription_date)}</TableCell>
                        <TableCell>{i.medicine_name}</TableCell>
                        <TableCell>{i.dosage} {i.frequency}</TableCell>
                        <TableCell>{i.duration_days} days</TableCell>
                        <TableCell>{p.doctor.full_name}</TableCell>
                      </TableRow>
                    )))}
                    {history.prescriptions.length === 0 && <TableRow><TableCell colSpan={5}>No approved prescriptions.</TableCell></TableRow>}
                  </TableBody>
                </Table>
              </Box>
            </Paper>

            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Laboratory reports</Typography>
              {history.lab_orders.map((o) => (
                <Box key={o.id} mb={2} sx={{ cursor: 'pointer' }} onClick={() => navigate(`/lab/orders/${o.id}`)}>
                  <Typography fontWeight={600}>{o.test.name} · {formatDate(o.report?.approved_at ?? o.created_at)}</Typography>
                  <Typography variant="body2">
                    {o.report?.results.map((r) => (
                      <Box component="span" key={r.code} sx={{ mr: 2, color: r.interpretation && r.interpretation !== 'N' ? 'error.main' : 'inherit' }}>
                        {r.name}: {r.value}{r.unit ? ` ${r.unit}` : ''}{r.interpretation && r.interpretation !== 'N' ? ` (${r.interpretation})` : ''}
                      </Box>
                    ))}
                  </Typography>
                </Box>
              ))}
              {history.lab_orders.length === 0 && <Typography color="text.secondary">No approved reports.</Typography>}
            </Paper>
          </Stack>
        </Grid>
      </Grid>
    </>
  )
}

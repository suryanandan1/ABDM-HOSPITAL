import { ReactNode, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Grid2 as Grid, List, ListItem, ListItemText, Paper, Skeleton, Table, TableBody, TableCell,
  TableHead, TableRow, Typography,
} from '@mui/material'
import PeopleIcon from '@mui/icons-material/People'
import TodayIcon from '@mui/icons-material/Today'
import VerifiedIcon from '@mui/icons-material/Verified'
import MedicalServicesIcon from '@mui/icons-material/MedicalServices'
import BadgeIcon from '@mui/icons-material/Badge'
import AssignmentIcon from '@mui/icons-material/Assignment'
import ScienceIcon from '@mui/icons-material/Science'
import FactCheckIcon from '@mui/icons-material/FactCheck'
import { api, ageOf, Dashboard as DashboardData, errorMessage, formatDateTime, GENDERS } from '../api'
import { useAuth } from '../auth'
import AbhaStatus from '../components/AbhaStatus'

function StatCard({ label, value, icon }: { label: string; value: number; icon: ReactNode }) {
  return (
    <Paper sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
      <Box sx={{ color: 'primary.main', bgcolor: '#e6f2f7', borderRadius: 2, p: 1.2, display: 'flex' }}>{icon}</Box>
      <Box>
        <Typography variant="h5" fontWeight={600}>{value}</Typography>
        <Typography variant="body2" color="text.secondary">{label}</Typography>
      </Box>
    </Paper>
  )
}

export default function Dashboard() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<DashboardData>('/dashboard').then((r) => setData(r.data)).catch((e) => setError(errorMessage(e)))
  }, [])

  if (error) return <Alert severity="error">{error}</Alert>
  if (!data) return <Skeleton variant="rounded" height={300} />

  const stats = [
    { label: 'Total patients', value: data.stats.total_patients, icon: <PeopleIcon /> },
    { label: 'Registered today', value: data.stats.patients_today, icon: <TodayIcon /> },
    { label: 'ABHA verified', value: data.stats.abha_verified_patients, icon: <VerifiedIcon /> },
    { label: 'Active doctors', value: data.stats.active_doctors, icon: <MedicalServicesIcon /> },
    { label: 'Active staff', value: data.stats.active_staff, icon: <BadgeIcon /> },
    { label: 'Consultations today', value: data.stats.consultations_today, icon: <AssignmentIcon /> },
    { label: 'Open lab orders', value: data.stats.lab_orders_open, icon: <ScienceIcon /> },
    { label: 'Lab reports to approve', value: data.stats.lab_reports_awaiting_approval, icon: <FactCheckIcon /> },
  ]

  return (
    <>
      <Typography variant="h5" mb={2}>Dashboard</Typography>
      <Grid container spacing={2} mb={3}>
        {stats.map((s) => (
          <Grid key={s.label} size={{ xs: 12, sm: 6, md: 3 }}>
            <StatCard {...s} />
          </Grid>
        ))}
      </Grid>

      <Grid container spacing={2}>
        {can('patient:read') && (
          <Grid size={{ xs: 12, lg: can('audit:read') ? 7 : 12 }}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Recent patients</Typography>
              <Box sx={{ overflowX: 'auto' }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Patient ID</TableCell>
                      <TableCell>Name</TableCell>
                      <TableCell>Age / Gender</TableCell>
                      <TableCell>ABHA</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.recent_patients.map((p) => (
                      <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/patients/${p.id}`)}>
                        <TableCell>{p.patient_code}</TableCell>
                        <TableCell>{p.full_name}</TableCell>
                        <TableCell>{ageOf(p)} / {GENDERS[p.gender]}</TableCell>
                        <TableCell><AbhaStatus patient={p} /></TableCell>
                      </TableRow>
                    ))}
                    {data.recent_patients.length === 0 && (
                      <TableRow><TableCell colSpan={4}>No patients registered yet.</TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
              </Box>
            </Paper>
          </Grid>
        )}
        {can('audit:read') && (
          <Grid size={{ xs: 12, lg: 5 }}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Recent activity</Typography>
              <List dense disablePadding>
                {data.recent_activity.map((a) => (
                  <ListItem key={a.id} disableGutters divider>
                    <ListItemText
                      primary={`${a.user_name ?? 'Unknown user'} — ${a.action}`}
                      secondary={`${a.entity_type ?? ''} ${a.entity_id ?? ''} · ${formatDateTime(a.created_at)}`}
                    />
                  </ListItem>
                ))}
              </List>
            </Paper>
          </Grid>
        )}
      </Grid>
    </>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Chip, LinearProgress, Paper, Tab, Table, TableBody, TableCell, TableHead, TablePagination, TableRow,
  Tabs, Typography,
} from '@mui/material'
import { formatDateTime, LabOrder } from '../api'
import { StatusChip } from '../components/clinical'
import { usePaged } from '../usePaged'

// Worklist tabs map to order statuses.
const TABS = [
  { label: 'Awaiting sample', status: 'ordered' },
  { label: 'Results to enter', status: 'sample_collected,in_progress' },
  { label: 'Completed', status: 'completed' },
  { label: 'All', status: '' },
]

export default function LabOrders() {
  const navigate = useNavigate()
  const [tab, setTab] = useState(0)
  const { data, loading, error, pagination } = usePaged<LabOrder>('/lab/orders', { status: TABS[tab].status })

  return (
    <>
      <Typography variant="h5" mb={2}>Laboratory orders</Typography>
      <Paper>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
          {TABS.map((t) => <Tab key={t.label} label={t.label} />)}
        </Tabs>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Order</TableCell>
                <TableCell>Ordered</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Test</TableCell>
                <TableCell>Priority</TableCell>
                <TableCell>Doctor</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Report</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((o) => (
                <TableRow key={o.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/lab/orders/${o.id}`)}>
                  <TableCell>#{o.id}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(o.created_at)}</TableCell>
                  <TableCell>{o.patient.full_name}<br /><Typography variant="caption">{o.patient.patient_code}</Typography></TableCell>
                  <TableCell>{o.test.name}</TableCell>
                  <TableCell>
                    <Chip size="small" label={o.priority.toUpperCase()} color={o.priority === 'routine' ? 'default' : 'error'} variant="outlined" />
                  </TableCell>
                  <TableCell>{o.doctor.full_name}</TableCell>
                  <TableCell><StatusChip status={o.status} /></TableCell>
                  <TableCell>{o.report ? <StatusChip status={o.report.status} /> : '—'}</TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={8}>No orders.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>
    </>
  )
}

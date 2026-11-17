import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Chip, Paper, Typography } from '@mui/material'
import { formatDateTime, LabOrder } from '../api'
import { StatusChip } from '../components/clinical'
import { FilterTabs, PagedTable, PatientCell } from '../components/ui'
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
  const paged = usePaged<LabOrder>('/lab/orders', { status: TABS[tab].status })

  return (
    <>
      <Typography variant="h5" mb={2}>Laboratory orders</Typography>
      <Paper>
        <FilterTabs labels={TABS.map((t) => t.label)} value={tab} onChange={setTab} />
        <PagedTable paged={paged} empty="No orders." onRowClick={(o) => navigate(`/lab/orders/${o.id}`)} columns={[
          ['Order', (o) => `#${o.id}`],
          ['Ordered', (o) => formatDateTime(o.created_at), { whiteSpace: 'nowrap' }],
          ['Patient', (o) => <PatientCell patient={o.patient} />],
          ['Test', (o) => o.test.name],
          ['Priority', (o) => <Chip size="small" label={o.priority.toUpperCase()} color={o.priority === 'routine' ? 'default' : 'error'} variant="outlined" />],
          ['Doctor', (o) => o.doctor.full_name],
          ['Status', (o) => <StatusChip status={o.status} />],
          ['Report', (o) => (o.report ? <StatusChip status={o.report.status} /> : '—')],
        ]} />
      </Paper>
    </>
  )
}

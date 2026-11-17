import { useState } from 'react'
import { MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { AuditLog, formatDateTime } from '../api'
import { PagedTable } from '../components/ui'
import { usePaged } from '../usePaged'

const ACTION_GROUPS = ['auth', 'patient', 'staff', 'doctor', 'hospital']

export default function AuditLogs() {
  const [action, setAction] = useState('')
  const paged = usePaged<AuditLog>('/audit-logs', { action }, 50)

  return (
    <>
      <Typography variant="h5" mb={2}>Audit logs</Typography>
      <Paper>
        <Stack direction="row" sx={{ p: 2 }}>
          <TextField select label="Area" value={action} onChange={(e) => setAction(e.target.value)} sx={{ width: 240 }}>
            <MenuItem value="">All activity</MenuItem>
            {ACTION_GROUPS.map((a) => <MenuItem key={a} value={`${a}.`}>{a[0].toUpperCase() + a.slice(1)}</MenuItem>)}
          </TextField>
        </Stack>
        <PagedTable paged={paged} rowsPerPage={[25, 50, 100]} columns={[
          ['Time', (log) => formatDateTime(log.created_at), { whiteSpace: 'nowrap' }],
          ['User', (log) => log.user_name ?? '—'],
          ['Action', (log) => log.action],
          ['Record', (log) => (log.entity_type ? `${log.entity_type} #${log.entity_id ?? ''}` : '—')],
          ['Details', (log) => (log.details ? JSON.stringify(log.details) : ''),
            { fontFamily: 'monospace', fontSize: 12, maxWidth: 420, wordBreak: 'break-word' }],
          ['IP', (log) => log.ip_address],
        ]} />
      </Paper>
    </>
  )
}

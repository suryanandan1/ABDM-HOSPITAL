import { useState } from 'react'
import {
  Alert, Box, LinearProgress, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TablePagination,
  TableRow, TextField, Typography,
} from '@mui/material'
import { AuditLog, formatDateTime } from '../api'
import { usePaged } from '../usePaged'

const ACTION_GROUPS = ['auth', 'patient', 'staff', 'doctor', 'hospital']

export default function AuditLogs() {
  const [action, setAction] = useState('')
  const { data, loading, error, pagination } = usePaged<AuditLog>('/audit-logs', { action }, 50)

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
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>User</TableCell>
                <TableCell>Action</TableCell>
                <TableCell>Record</TableCell>
                <TableCell>Details</TableCell>
                <TableCell>IP</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((log) => (
                <TableRow key={log.id}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(log.created_at)}</TableCell>
                  <TableCell>{log.user_name ?? '—'}</TableCell>
                  <TableCell>{log.action}</TableCell>
                  <TableCell>{log.entity_type ? `${log.entity_type} #${log.entity_id ?? ''}` : '—'}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: 12, maxWidth: 420, wordBreak: 'break-word' }}>
                    {log.details ? JSON.stringify(log.details) : ''}
                  </TableCell>
                  <TableCell>{log.ip_address}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[25, 50, 100]} {...pagination} />
      </Paper>
    </>
  )
}

import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Grid2 as Grid,
  LinearProgress, MenuItem, Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TablePagination, TableRow,
  TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { api, Doctor, errorMessage, Page, User } from '../api'
import { useAuth } from '../auth'
import { usePaged } from '../usePaged'

const FIELDS = [
  ['full_name', 'Full name'], ['registration_number', 'Medical registration number'], ['hpr_id', 'HPR ID (ABDM)'],
  ['specialization', 'Specialization'], ['qualification', 'Qualification'], ['department', 'Department'],
  ['phone', 'Phone'], ['email', 'Email'],
] as const

export default function Doctors() {
  const { can } = useAuth()
  const [q, setQ] = useState('')
  const [active, setActive] = useState('true')
  const [editing, setEditing] = useState<Doctor | 'new' | null>(null)
  const { data, loading, error, reload, pagination } = usePaged<Doctor>('/doctors', { q, is_active: active })
  const canManage = can('doctor:manage')

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Doctors</Typography>
        {canManage && <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Add doctor</Button>}
      </Stack>
      <Paper>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ p: 2 }}>
          <TextField placeholder="Search name, specialization, department or registration no." value={q} onChange={(e) => setQ(e.target.value)} />
          <TextField select label="Status" value={active} onChange={(e) => setActive(e.target.value)} sx={{ width: { sm: 200 } }}>
            <MenuItem value="">All</MenuItem>
            <MenuItem value="true">Active</MenuItem>
            <MenuItem value="false">Inactive</MenuItem>
          </TextField>
        </Stack>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Specialization</TableCell>
                <TableCell>Department</TableCell>
                <TableCell>Registration no.</TableCell>
                <TableCell>Login account</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((d) => (
                <TableRow key={d.id} hover={canManage} sx={{ cursor: canManage ? 'pointer' : 'default' }} onClick={() => canManage && setEditing(d)}>
                  <TableCell>{d.full_name}<br /><Typography variant="caption" color="text.secondary">{d.qualification}</Typography></TableCell>
                  <TableCell>{d.specialization}</TableCell>
                  <TableCell>{d.department}</TableCell>
                  <TableCell>{d.registration_number}</TableCell>
                  <TableCell>{d.username ?? '—'}</TableCell>
                  <TableCell><Chip size="small" label={d.is_active ? 'Active' : 'Inactive'} color={d.is_active ? 'success' : 'default'} /></TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No doctors found.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>

      {editing && (
        <DoctorDialog doctor={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload() }} />
      )}
    </>
  )
}

function DoctorDialog({ doctor, onClose, onSaved }: { doctor: Doctor | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<Record<string, string>>(
    Object.fromEntries(FIELDS.map(([name]) => [name, doctor?.[name] ?? ''])),
  )
  const [userId, setUserId] = useState<string>(doctor?.user_id ? String(doctor.user_id) : '')
  const [isActive, setIsActive] = useState(doctor?.is_active ?? true)
  const [doctorUsers, setDoctorUsers] = useState<User[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<Page<User>>('/staff', { params: { role: 'doctor', is_active: true, page_size: 100 } })
      .then((r) => setDoctorUsers(r.data.items))
  }, [])

  const save = async () => {
    const body = { ...form, user_id: userId ? Number(userId) : null, ...(doctor && { is_active: isActive }) }
    try {
      if (doctor) await api.patch(`/doctors/${doctor.id}`, body)
      else await api.post('/doctors', body)
      onSaved()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{doctor ? 'Edit doctor' : 'Add doctor'}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2} pt={1}>
          {FIELDS.map(([name, label]) => (
            <Grid key={name} size={{ xs: 12, sm: name === 'full_name' ? 12 : 6 }}>
              <TextField label={label} value={form[name]} required={name === 'full_name' || name === 'registration_number'}
                onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
            </Grid>
          ))}
          <Grid size={12}>
            <TextField select label="Linked login account" value={userId} onChange={(e) => setUserId(e.target.value)}
              helperText="Optional: a staff account with the Doctor role">
              <MenuItem value="">None</MenuItem>
              {doctorUsers.map((u) => <MenuItem key={u.id} value={String(u.id)}>{u.full_name} ({u.username})</MenuItem>)}
            </TextField>
          </Grid>
          {doctor && (
            <Grid size={12}>
              <FormControlLabel label="Active" control={<Switch checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />} />
            </Grid>
          )}
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  )
}

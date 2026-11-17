import { useEffect, useState } from 'react'
import {
  Alert, Button, FormControlLabel, Grid2 as Grid, MenuItem, Paper, Stack, Switch, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { api, Doctor, errorMessage, Page, User } from '../api'
import { useAuth } from '../auth'
import { ActiveChip, FormDialog, PagedTable, PageHeader } from '../components/ui'
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
  const paged = usePaged<Doctor>('/doctors', { q, is_active: active })
  const canManage = can('doctor:manage')

  return (
    <>
      <PageHeader title="Doctors">
        {canManage && <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Add doctor</Button>}
      </PageHeader>
      <Paper>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ p: 2 }}>
          <TextField placeholder="Search name, specialization, department or registration no." value={q} onChange={(e) => setQ(e.target.value)} />
          <TextField select label="Status" value={active} onChange={(e) => setActive(e.target.value)} sx={{ width: { sm: 200 } }}>
            <MenuItem value="">All</MenuItem>
            <MenuItem value="true">Active</MenuItem>
            <MenuItem value="false">Inactive</MenuItem>
          </TextField>
        </Stack>
        <PagedTable paged={paged} empty="No doctors found." onRowClick={canManage ? setEditing : undefined} columns={[
          ['Name', (d) => <>{d.full_name}<br /><Typography variant="caption" color="text.secondary">{d.qualification}</Typography></>],
          ['Specialization', (d) => d.specialization],
          ['Department', (d) => d.department],
          ['Registration no.', (d) => d.registration_number],
          ['Login account', (d) => d.username ?? '—'],
          ['Status', (d) => <ActiveChip active={d.is_active} />],
        ]} />
      </Paper>

      {editing && (
        <DoctorDialog doctor={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); paged.reload() }} />
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
    <FormDialog title={doctor ? 'Edit doctor' : 'Add doctor'} onClose={onClose}
      actions={<Button variant="contained" onClick={save}>Save</Button>}>
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
    </FormDialog>
  )
}

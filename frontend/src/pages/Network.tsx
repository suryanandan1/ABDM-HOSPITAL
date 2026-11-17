import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Button, Chip, Divider, Grid2 as Grid, LinearProgress, MenuItem, Paper, Stack, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { api, errorMessage, formatDateTime, ManagedHospital, Role, User } from '../api'
import { ActiveChip, DataTable, FormDialog, PagedTable, PageHeader, StatTiles } from '../components/ui'
import { usePaged } from '../usePaged'
import { Field, FIELDS } from './HospitalProfile'
import { StaffDialog } from './Staff'

const PRIVACY_NOTE = 'As network administrator you manage hospitals and their staff accounts. Patient and clinical ' +
  'records stay with each hospital: only its own staff can open them, and they move between hospitals only through ' +
  'ABDM with the patient\'s consent.'

/** Every hospital on the network: status, ABDM IDs, administrators and counts. */
export default function Network() {
  const navigate = useNavigate()
  const [hospitals, setHospitals] = useState<ManagedHospital[] | null>(null)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => {
    api.get<ManagedHospital[]>('/network/hospitals').then((r) => setHospitals(r.data)).catch((e) => setError(errorMessage(e)))
  }, [])
  useEffect(load, [load])

  const sum = (key: keyof ManagedHospital['stats']) => hospitals?.reduce((n, h) => n + h.stats[key], 0)
  return (
    <>
      <PageHeader title="Hospitals">
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAdding(true)}>Add hospital</Button>
      </PageHeader>
      <Typography variant="body2" color="text.secondary" mb={2}>{PRIVACY_NOTE}</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {!hospitals ? <LinearProgress /> : (
        <Stack spacing={2}>
          <StatTiles tiles={[
            ['Hospitals', hospitals.length], ['Active hospitals', hospitals.filter((h) => h.is_active).length],
            ['Staff accounts', sum('staff')], ['Records shared through ABDM', sum('shared_records')],
          ]} />
          <Paper>
            <DataTable rows={hospitals} onRowClick={(h) => navigate(`/network/hospitals/${h.id}`)} empty="No hospitals yet" columns={[
              ['Hospital', (h) => <>{h.name}<Typography variant="caption" display="block">{h.code} · {[h.city, h.state].filter(Boolean).join(', ')}</Typography></>],
              ['ABDM HIP / HIU', (h) => <>{h.abdm_hip_id ?? '—'}<Typography variant="caption" display="block">{h.abdm_hiu_id ?? '—'}</Typography></>],
              ['Administrator', (h) => h.admins.length ? h.admins.map((a) => (
                <Typography key={a.id} variant="body2" color={a.is_active ? undefined : 'text.disabled'}>{a.username}</Typography>
              )) : <Typography variant="caption" color="error">none</Typography>],
              ['Staff', (h) => `${h.stats.active_staff} active / ${h.stats.staff}`],
              ['Doctors', (h) => h.stats.doctors],
              ['Patients', (h) => h.stats.patients],
              ['Shared records', (h) => h.stats.shared_records],
              ['Status', (h) => <ActiveChip active={h.is_active} />],
            ]} />
          </Paper>
        </Stack>
      )}
      {adding && <AddHospitalDialog onClose={() => setAdding(false)} onSaved={(h) => navigate(`/network/hospitals/${h.id}`)} />}
    </>
  )
}

function AddHospitalDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (h: ManagedHospital) => void }) {
  const [form, setForm] = useState({ name: '', code: '', city: '', state: '', facility_type: '', phone: '', email: '',
    abdm_hip_id: '', abdm_hiu_id: '' })
  const [admin, setAdmin] = useState({ username: '', full_name: '', password: '' })
  const [error, setError] = useState('')
  const code = form.code.trim().toUpperCase()

  const save = async () => {
    try {
      const body = { ...form, admin: admin.username || admin.password ? admin : undefined }
      onSaved((await api.post<ManagedHospital>('/network/hospitals', body)).data)
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const field = (name: keyof typeof form, label: string, extra: object = {}) => (
    <TextField label={label} value={form[name]} onChange={(e) => setForm({ ...form, [name]: e.target.value })} {...extra} />
  )
  return (
    <FormDialog title="Add hospital" onClose={onClose} maxWidth="md" actions={<Button variant="contained" onClick={save}>Add hospital</Button>}>
      <Stack spacing={2} pt={1}>
        {error && <Alert severity="error">{error}</Alert>}
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 8 }}>{field('name', 'Hospital name', { required: true })}</Grid>
          <Grid size={{ xs: 12, sm: 4 }}>{field('code', 'Code (patient ID prefix)', { required: true, helperText: '2-10 letters/digits, e.g. LKH; cannot be changed later' })}</Grid>
          <Grid size={{ xs: 12, sm: 4 }}>{field('city', 'City')}</Grid>
          <Grid size={{ xs: 12, sm: 4 }}>{field('state', 'State')}</Grid>
          <Grid size={{ xs: 12, sm: 4 }}>{field('facility_type', 'Facility type')}</Grid>
          <Grid size={{ xs: 12, sm: 6 }}>{field('phone', 'Phone')}</Grid>
          <Grid size={{ xs: 12, sm: 6 }}>{field('email', 'Email')}</Grid>
          <Grid size={{ xs: 12, sm: 6 }}>{field('abdm_hip_id', 'ABDM HIP ID', { helperText: `Blank in simulation: SIM_HIP_${code || '<code>'}` })}</Grid>
          <Grid size={{ xs: 12, sm: 6 }}>{field('abdm_hiu_id', 'ABDM HIU ID', { helperText: `Blank in simulation: SIM_HIU_${code || '<code>'}` })}</Grid>
        </Grid>
        <Divider />
        <Typography variant="subtitle2">Hospital administrator (manages this hospital only)</Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField label="Username" value={admin.username} placeholder={code ? `${code.toLowerCase()}.admin` : ''}
              onChange={(e) => setAdmin({ ...admin, username: e.target.value })} />
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField label="Full name" value={admin.full_name} onChange={(e) => setAdmin({ ...admin, full_name: e.target.value })} />
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField type="password" label="Password" value={admin.password} helperText="At least 8 characters with letters and digits"
              onChange={(e) => setAdmin({ ...admin, password: e.target.value })} />
          </Grid>
        </Grid>
        <Typography variant="caption" color="text.secondary">Leave the administrator blank to add one later from the hospital's page.</Typography>
      </Stack>
    </FormDialog>
  )
}

/** One hospital: status, profile and ABDM IDs, and its staff accounts. */
export function NetworkHospitalPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [hospital, setHospital] = useState<ManagedHospital | null>(null)
  const [form, setForm] = useState<Record<Field, string> | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [q, setQ] = useState('')
  const [role, setRole] = useState('')
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const staff = usePaged<User>(`/network/hospitals/${id}/staff`, { q, role })

  const show = (h: ManagedHospital) => {
    setHospital(h)
    setForm(Object.fromEntries(FIELDS.map(([name]) => [name, h[name] ?? ''])) as Record<Field, string>)
  }
  const load = useCallback(() => {
    api.get<ManagedHospital>(`/network/hospitals/${id}`).then((r) => show(r.data))
      .catch((e) => setMessage({ ok: false, text: errorMessage(e) }))
  }, [id])
  useEffect(load, [load])
  useEffect(() => { api.get<Role[]>('/network/roles').then((r) => setRoles(r.data)) }, [])

  const patch = async (body: object, done: string) => {
    try {
      show((await api.patch<ManagedHospital>(`/network/hospitals/${id}`, body)).data)
      setMessage({ ok: true, text: done })
    } catch (e) {
      setMessage({ ok: false, text: errorMessage(e) })
    }
  }
  const toggle = () => {
    if (!hospital) return
    const off = hospital.is_active
    if (off && !window.confirm(`Deactivate ${hospital.name}? All its staff are signed out and cannot sign in, and ABDM ` +
      'no longer routes consent or record requests to it. Its data is kept; you can activate it again.')) return
    patch({ is_active: !off }, off ? `${hospital.name} deactivated.` : `${hospital.name} activated.`)
  }

  if (!hospital || !form) return message ? <Alert severity="error">{message.text}</Alert> : <LinearProgress />
  return (
    <>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/network')} sx={{ mb: 1 }}>All hospitals</Button>
      <Stack direction="row" alignItems="center" spacing={1} mb={2} flexWrap="wrap" useFlexGap>
        <Typography variant="h5">{hospital.name}</Typography>
        <Chip size="small" variant="outlined" label={hospital.code} />
        <ActiveChip active={hospital.is_active} />
        <span style={{ flexGrow: 1 }} />
        <Button color={hospital.is_active ? 'warning' : 'success'} variant="outlined" onClick={toggle}>
          {hospital.is_active ? 'Deactivate hospital' : 'Activate hospital'}
        </Button>
      </Stack>
      {message && <Alert severity={message.ok ? 'success' : 'error'} sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message.text}</Alert>}
      <Stack spacing={2}>
        <StatTiles tiles={[
          ['Active staff', hospital.stats.active_staff], ['Doctors', hospital.stats.doctors],
          ['Patients', hospital.stats.patients], ['Records shared through ABDM', hospital.stats.shared_records],
        ]} />

        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="subtitle1" fontWeight={600}>Staff accounts</Typography>
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Add staff</Button>
          </Stack>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} mb={1}>
            <TextField placeholder="Search name, username or email" value={q} onChange={(e) => setQ(e.target.value)} />
            <TextField select label="Role" value={role} onChange={(e) => setRole(e.target.value)} sx={{ width: { sm: 300 } }}>
              <MenuItem value="">All roles</MenuItem>
              {roles.map((r) => <MenuItem key={r.code} value={r.code}>{r.name}</MenuItem>)}
            </TextField>
          </Stack>
          <PagedTable paged={staff} onRowClick={setEditing} empty="No staff accounts" columns={[
            ['Name', (u) => u.full_name],
            ['Username', (u) => u.username],
            ['Role', (u) => u.role_name],
            ['Last login', (u) => formatDateTime(u.last_login_at) || 'Never'],
            ['Status', (u) => <>
              <ActiveChip active={u.is_active} />
              {u.mfa_enabled && <Chip size="small" variant="outlined" label="2-step" sx={{ ml: 0.5 }} />}
            </>],
          ]} />
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="subtitle1" fontWeight={600} mb={2}>Profile and ABDM identity</Typography>
          <Grid container spacing={2}>
            {FIELDS.map(([name, label, width]) => (
              <Grid key={name} size={{ xs: 12, sm: width }}>
                <TextField label={label} value={form[name]} required={name === 'name'}
                  onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
              </Grid>
            ))}
          </Grid>
          <Stack direction="row" justifyContent="flex-end" mt={2}>
            <Button variant="contained" onClick={() => patch(form, 'Hospital profile saved.')}>Save profile</Button>
          </Stack>
        </Paper>
      </Stack>

      {editing && (
        <StaffDialog staff={editing === 'new' ? null : editing} roles={roles} isSelf={false}
          createUrl={`/network/hospitals/${id}/staff`} itemUrl={(userId) => `/network/staff/${userId}`}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); staff.reload(); load() }} />
      )}
    </>
  )
}

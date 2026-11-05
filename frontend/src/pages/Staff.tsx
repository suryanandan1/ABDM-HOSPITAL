import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, LinearProgress,
  MenuItem, Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TablePagination, TableRow, TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { api, errorMessage, formatDateTime, Role, User } from '../api'
import { useAuth } from '../auth'
import { usePaged } from '../usePaged'

export default function Staff() {
  const { can, user: me } = useAuth()
  const [q, setQ] = useState('')
  const [role, setRole] = useState('')
  const [roles, setRoles] = useState<Role[]>([])
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const { data, loading, error, reload, pagination } = usePaged<User>('/staff', { q, role })

  useEffect(() => {
    api.get<Role[]>('/roles').then((r) => setRoles(r.data))
  }, [])

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Staff</Typography>
        {can('staff:manage') && <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Add staff</Button>}
      </Stack>
      <Paper>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ p: 2 }}>
          <TextField placeholder="Search name, username or email" value={q} onChange={(e) => setQ(e.target.value)} />
          <TextField select label="Role" value={role} onChange={(e) => setRole(e.target.value)} sx={{ width: { sm: 300 } }}>
            <MenuItem value="">All roles</MenuItem>
            {roles.map((r) => <MenuItem key={r.code} value={r.code}>{r.name}</MenuItem>)}
          </TextField>
        </Stack>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Username</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Contact</TableCell>
                <TableCell>Last login</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((u) => (
                <TableRow key={u.id} hover={can('staff:manage')} sx={{ cursor: can('staff:manage') ? 'pointer' : 'default' }}
                  onClick={() => can('staff:manage') && setEditing(u)}>
                  <TableCell>{u.full_name}{u.id === me?.id && ' (you)'}</TableCell>
                  <TableCell>{u.username}</TableCell>
                  <TableCell>{u.role_name}</TableCell>
                  <TableCell>{[u.email, u.phone].filter(Boolean).join(' · ')}</TableCell>
                  <TableCell>{formatDateTime(u.last_login_at) || 'Never'}</TableCell>
                  <TableCell>
                    <Chip size="small" label={u.is_active ? 'Active' : 'Inactive'} color={u.is_active ? 'success' : 'default'} />
                    {u.mfa_enabled && <Chip size="small" variant="outlined" label="2-step" sx={{ ml: 0.5 }} />}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>

      {editing && (
        <StaffDialog staff={editing === 'new' ? null : editing} roles={roles} isSelf={editing !== 'new' && editing.id === me?.id}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />
      )}
    </>
  )
}

function StaffDialog({ staff, roles, isSelf, onClose, onSaved }: {
  staff: User | null; roles: Role[]; isSelf: boolean; onClose: () => void; onSaved: () => void
}) {
  const [form, setForm] = useState({
    username: staff?.username ?? '',
    full_name: staff?.full_name ?? '',
    email: staff?.email ?? '',
    phone: staff?.phone ?? '',
    role: staff?.role_code ?? 'receptionist',
    is_active: staff?.is_active ?? true,
    password: '',
  })
  const [error, setError] = useState('')
  const set = (name: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [name]: e.target.value })

  const save = async () => {
    try {
      if (staff) {
        const { username: _, password, ...rest } = form
        await api.patch(`/staff/${staff.id}`, password ? { ...rest, password } : rest)
      } else {
        const { is_active: _, ...rest } = form
        await api.post('/staff', rest)
      }
      onSaved()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const resetMfa = async () => {
    if (!staff || !window.confirm(`Turn off two-step sign-in for ${staff.full_name}? They will be signed out.`)) return
    try {
      await api.delete(`/staff/${staff.id}/mfa`)
      onSaved()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const selectedRole = roles.find((r) => r.code === form.role)

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{staff ? 'Edit staff member' : 'Add staff member'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} pt={1}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="Username" value={form.username} onChange={set('username')} disabled={Boolean(staff)} required />
          <TextField label="Full name" value={form.full_name} onChange={set('full_name')} required />
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField label="Email" value={form.email} onChange={set('email')} />
            <TextField label="Phone" value={form.phone} onChange={set('phone')} />
          </Stack>
          <TextField select label="Role" value={form.role} onChange={set('role')} disabled={isSelf}
            helperText={isSelf ? 'You cannot change your own role' : selectedRole?.description}>
            {roles.map((r) => <MenuItem key={r.code} value={r.code}>{r.name}</MenuItem>)}
          </TextField>
          <TextField type="password" label={staff ? 'New password (leave blank to keep)' : 'Password'} value={form.password}
            onChange={set('password')} required={!staff} helperText="At least 8 characters with letters and digits" />
          {staff && (
            <FormControlLabel label="Active" disabled={isSelf}
              control={<Switch checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />} />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        {staff?.mfa_enabled && !isSelf && (
          <Button color="warning" onClick={resetMfa} sx={{ mr: 'auto' }}>Reset two-step sign-in</Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  )
}

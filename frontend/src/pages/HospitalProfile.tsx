import { useEffect, useState } from 'react'
import { Alert, Button, Grid2 as Grid, Paper, Skeleton, Stack, TextField, Typography } from '@mui/material'
import { api, errorMessage, Hospital } from '../api'
import { useAuth } from '../auth'

const FIELDS = [
  ['name', 'Hospital name', 12], ['hfr_id', 'HFR ID (ABDM Health Facility Registry)', 6],
  ['facility_type', 'Facility type', 6], ['address', 'Address', 12], ['city', 'City', 4], ['state', 'State', 4],
  ['pincode', 'Pincode', 4], ['phone', 'Phone', 6], ['email', 'Email', 6],
] as const

type Field = (typeof FIELDS)[number][0]

export default function HospitalProfile() {
  const { can } = useAuth()
  const editable = can('hospital:update')
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [form, setForm] = useState<Record<Field, string> | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = (h: Hospital) => {
    setHospital(h)
    setForm(Object.fromEntries(FIELDS.map(([name]) => [name, h[name] ?? ''])) as Record<Field, string>)
  }

  useEffect(() => {
    api.get<Hospital>('/hospital').then((r) => load(r.data)).catch((e) => setError(errorMessage(e)))
  }, [])

  const save = async () => {
    try {
      load((await api.put<Hospital>('/hospital', form)).data)
      setMessage('Hospital profile saved.')
      setError('')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  if (!form || !hospital) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />

  return (
    <>
      <Typography variant="h5" mb={2}>Hospital profile</Typography>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper sx={{ p: 3, maxWidth: 900 }}>
        <Typography variant="body2" color="text.secondary" mb={2}>
          Hospital code <b>{hospital.code}</b> is used as the prefix for patient IDs and cannot be changed.
        </Typography>
        <Grid container spacing={2}>
          {FIELDS.map(([name, label, width]) => (
            <Grid key={name} size={{ xs: 12, sm: width }}>
              <TextField label={label} value={form[name]} disabled={!editable} required={name === 'name'}
                onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
            </Grid>
          ))}
        </Grid>
        {editable && (
          <Stack direction="row" justifyContent="flex-end" mt={3}>
            <Button variant="contained" onClick={save}>Save</Button>
          </Stack>
        )}
      </Paper>
    </>
  )
}

import { FormEvent, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { Alert, Box, Button, Paper, Stack, TextField, Typography } from '@mui/material'
import LocalHospitalIcon from '@mui/icons-material/LocalHospital'
import { errorMessage } from '../api'
import { useAuth } from '../auth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [needsCode, setNeedsCode] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await login(username, password, needsCode ? otp : undefined)
      navigate('/')
    } catch (err) {
      const code = axios.isAxiosError(err) ? err.response?.data?.detail?.code : undefined
      if (code === 'mfa_required') {
        setNeedsCode(true)
        setError('')
      } else {
        setError(errorMessage(err))
        if (code === 'mfa_invalid') setOtp('')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
      <Paper component="form" onSubmit={submit} sx={{ p: 4, width: '100%', maxWidth: 400 }}>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} alignItems="center">
            <LocalHospitalIcon color="primary" fontSize="large" />
            <Box>
              <Typography variant="h6">ABDM Hospital Platform</Typography>
              <Typography variant="body2" color="text.secondary">Sign in to continue</Typography>
            </Box>
          </Stack>
          {params.get('idle') && !error && (
            <Alert severity="info">You were signed out after a period without activity.</Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {needsCode ? (
            <>
              <Typography variant="body2">Enter the 6-digit code from your authenticator app.</Typography>
              <TextField label="Code" value={otp} onChange={(e) => setOtp(e.target.value)} autoFocus required
                inputProps={{ inputMode: 'numeric', autoComplete: 'one-time-code', maxLength: 8 }} />
              <Button onClick={() => { setNeedsCode(false); setOtp(''); setPassword('') }}>Back</Button>
            </>
          ) : (
            <>
              <TextField label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
              <TextField label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </>
          )}
          <Button type="submit" variant="contained" size="large" disabled={busy}>
            {busy ? 'Signing in…' : needsCode ? 'Verify' : 'Sign in'}
          </Button>
        </Stack>
      </Paper>
    </Box>
  )
}

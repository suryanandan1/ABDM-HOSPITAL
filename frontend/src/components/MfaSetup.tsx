import { useState } from 'react'
import { Alert, Box, Button, Link, Stack, TextField, Typography } from '@mui/material'
import { api, errorMessage } from '../api'
import { useAuth } from '../auth'

/** Turn two-step sign-in (authenticator app) on or off for the signed-in user. */
export default function MfaSetup({ onDone }: { onDone?: () => void }) {
  const { user, refresh } = useAuth()
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const start = () => run(async () => {
    const { data } = await api.post('/auth/mfa/setup')
    setSetup(data)
    setCode('')
  })

  const enable = () => run(async () => {
    await api.post('/auth/mfa/enable', { code })
    setSetup(null)
    setMessage('Two-step sign-in is on. Your other sessions were signed out.')
    await refresh()
    onDone?.()
  })

  const disable = () => run(async () => {
    await api.post('/auth/mfa/disable', { password, code })
    setPassword('')
    setCode('')
    setMessage('Two-step sign-in is off.')
    await refresh()
  })

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {message && <Alert severity="success">{message}</Alert>}
      {user?.mfa_enabled ? (
        <>
          <Typography variant="body2">
            Two-step sign-in is <b>on</b>: signing in needs your password and a code from your authenticator app.
          </Typography>
          {user.mfa_setup_required ? null : (
            <>
              <Typography variant="body2" color="text.secondary">To turn it off, enter your password and a current code.</Typography>
              <TextField type="password" label="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <TextField label="Code" value={code} onChange={(e) => setCode(e.target.value)} inputProps={{ inputMode: 'numeric', maxLength: 8 }} />
              <Box><Button color="error" onClick={disable} disabled={busy || !password || !code}>Turn off</Button></Box>
            </>
          )}
        </>
      ) : setup ? (
        <>
          <Typography variant="body2">
            1. In an authenticator app (Google Authenticator, Microsoft Authenticator, FreeOTP…), add an account
            with this key, or open the link on your phone:
          </Typography>
          <Typography sx={{ fontFamily: 'monospace', fontSize: 18, letterSpacing: 1, wordBreak: 'break-all' }}>
            {setup.secret.match(/.{1,4}/g)?.join(' ')}
          </Typography>
          <Link href={setup.otpauth_uri} variant="body2">Open in authenticator app</Link>
          <Typography variant="body2">2. Enter the 6-digit code the app shows:</Typography>
          <TextField label="Code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus
            inputProps={{ inputMode: 'numeric', autoComplete: 'one-time-code', maxLength: 8 }} />
          <Box><Button variant="contained" onClick={enable} disabled={busy || !code}>Turn on</Button></Box>
        </>
      ) : (
        <>
          <Typography variant="body2">
            Two-step sign-in is <b>off</b>. With it on, signing in also needs a code from an authenticator app on
            your phone, so a stolen password alone is not enough.
          </Typography>
          <Box><Button variant="contained" onClick={start} disabled={busy}>Set up</Button></Box>
        </>
      )}
    </Stack>
  )
}

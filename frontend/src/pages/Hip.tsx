import { Fragment, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Collapse, Grid2 as Grid, IconButton, LinearProgress, MenuItem, Paper, Stack, Tab,
  Table, TableBody, TableCell, TableHead, TablePagination, TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import {
  api, CareContextRow, errorMessage, formatDateTime, HipConsentRow, HipPatientOverview, HipStatus, HipTransactionRow,
  PatientSummary,
} from '../api'
import { useAuth } from '../auth'
import { PatientBanner, PatientPicker } from '../components/clinical'
import { usePaged } from '../usePaged'

const KIND_LABEL: Record<string, string> = {
  session: 'Gateway session', bridge_url: 'Bridge URL', generate_link_token: 'ABHA verification request',
  on_generate_token: 'ABHA verification result', add_care_contexts: 'Care context link request',
  on_add_care_contexts: 'Care context link result', discover: 'Discovery', link_init: 'Patient link request (OTP)',
  link_confirm: 'Patient link confirmation', consent_notify: 'Consent notification', health_information: 'Health information request',
  callback_rejected: 'Rejected callback',
}
const STATUS_COLOR = { success: 'success', failed: 'error', pending: 'warning' } as const
const LINK_COLOR: Record<string, 'default' | 'success' | 'error' | 'warning' | 'info'> = {
  linked: 'success', verified: 'success', GRANTED: 'success', failed: 'error', REJECTED: 'error', linking: 'warning',
  pending: 'warning', otp_sent: 'info', REVOKED: 'default', EXPIRED: 'default', not_linked: 'default',
}

export const ModeChip = ({ mode }: { mode: string }) => (
  <Chip size="small" label={mode.toUpperCase()} color={mode === 'sandbox' ? 'secondary' : 'info'} variant="outlined" />
)

export default function Hip() {
  const [tab, setTab] = useState(0)
  const [status, setStatus] = useState<HipStatus | null>(null)
  const loadStatus = () => api.get<HipStatus>('/hip/status').then((r) => setStatus(r.data))
  useEffect(() => { loadStatus() }, [])

  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center" mb={2}>
        <Typography variant="h5">ABDM Health Information Provider</Typography>
        {status && <ModeChip mode={status.mode} />}
      </Stack>
      <Alert severity="info" sx={{ mb: 2 }}>
        The hospital keeps its records. ABDM only routes discovery, linking and consent; records are encrypted and sent
        directly to the requesting provider when the patient has granted consent.
      </Alert>
      <Paper>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
          <Tab label="Overview" />
          <Tab label="Patients & linking" />
          <Tab label="Care contexts" />
          <Tab label="Requests & consents" />
          <Tab label="Transaction log" />
        </Tabs>
        <Box sx={{ p: 2 }}>
          {tab === 0 && <Overview status={status} onChange={loadStatus} />}
          {tab === 1 && <PatientLinking mode={status?.mode} onChange={loadStatus} />}
          {tab === 2 && <CareContexts />}
          {tab === 3 && <Requests />}
          {tab === 4 && <TransactionLog />}
        </Box>
      </Paper>
    </>
  )
}

function Overview({ status, onChange }: { status: HipStatus | null; onChange: () => void }) {
  const { can } = useAuth()
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  if (!status) return <LinearProgress />
  const act = (path: string) => api.post<{ status: string; message: string }>(path)
    .then((r) => { setMessage({ ok: r.data.status === 'success', text: r.data.message }); onChange() })
    .catch((e) => setMessage({ ok: false, text: errorMessage(e) }))
  const c = status.counts
  const tiles = [
    ['Verified ABHA links', c.verified_links], ['Care contexts', c.care_contexts], ['Linked care contexts', c.linked_care_contexts],
    ['Discovery requests', c.discovery_requests], ['Active consents', c.active_consents], ['Pending transfers', c.pending_transfers],
    ['Completed transfers', c.completed_transfers], ['Failed exchanges', c.failed],
  ] as const
  return (
    <Stack spacing={2}>
      {!status.ready && <Alert severity="warning">The HIP is not fully configured. Fix the items marked below (backend .env).</Alert>}
      <Table size="small">
        <TableBody>
          {status.checks.map((check) => (
            <TableRow key={check.label}>
              <TableCell sx={{ width: 32 }}>{check.ok ? <CheckCircleIcon color="success" fontSize="small" /> : <ErrorIcon color="error" fontSize="small" />}</TableCell>
              <TableCell sx={{ width: 200 }}><b>{check.label}</b></TableCell>
              <TableCell>{check.detail}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Grid container spacing={1.5}>
        {tiles.map(([label, value]) => (
          <Grid key={label} size={{ xs: 6, md: 3 }}>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="h6">{value ?? 0}</Typography>
              <Typography variant="body2" color="text.secondary">{label}</Typography>
            </Paper>
          </Grid>
        ))}
      </Grid>
      {can('hip:configure') && (
        <Stack direction="row" spacing={1}>
          <Button variant="outlined" onClick={() => act('/hip/session-check')}>Check gateway session</Button>
          <Button variant="outlined" onClick={() => act('/hip/bridge-url')}>Register bridge URL with ABDM</Button>
        </Stack>
      )}
      {message && <Alert severity={message.ok ? 'success' : 'error'} onClose={() => setMessage(null)}>{message.text}</Alert>}
    </Stack>
  )
}

function PatientLinking({ mode, onChange }: { mode?: string; onChange: () => void }) {
  const { can } = useAuth()
  const [patient, setPatient] = useState<PatientSummary | null>(null)
  const [overview, setOverview] = useState<HipPatientOverview | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const manage = can('hip:manage')

  const load = (p = patient) => p && api.get<HipPatientOverview>(`/hip/patients/${p.id}`).then((r) => { setOverview(r.data); setSelected([]) })
  useEffect(() => { load() }, [patient])

  const act = async (request: () => Promise<{ data: { status?: string; message?: string } }>) => {
    try {
      const { data } = await request()
      setMessage({ ok: data.status !== 'failed', text: data.message ?? 'Done' })
    } catch (e) {
      setMessage({ ok: false, text: errorMessage(e) })
    }
    await load()
    onChange()
  }

  return (
    <Stack spacing={2}>
      <PatientPicker onChange={setPatient} />
      {message && <Alert severity={message.ok ? 'success' : 'error'} onClose={() => setMessage(null)}>{message.text}</Alert>}
      {overview && (
        <>
          <PatientBanner patient={overview.patient} />
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight={600}>ABHA identity</Typography>
            <Typography variant="body2" mb={1}>
              ABHA address: <b>{overview.patient.abha_address ?? 'not recorded'}</b> ·{' '}
              {overview.patient.abha_verified ? `verified (${overview.abha_verification_method})` : 'not verified — entered values are never trusted'}
            </Typography>
            {overview.link && (
              <Typography variant="body2" mb={1}>
                ABDM verification: <Chip size="small" label={overview.link.status} color={LINK_COLOR[overview.link.status]} /> {overview.link.message}{' '}
                <ModeChip mode={overview.link.mode} />
              </Typography>
            )}
            {manage && (
              <Button variant="outlined" disabled={!overview.patient.abha_address}
                onClick={() => act(() => api.post(`/hip/patients/${overview.patient.id}/link-token`))}>
                Verify ABHA address with ABDM
              </Button>
            )}
            {!overview.patient.abha_address && <Typography variant="caption" display="block">Record the ABHA address on the patient profile first, or let the patient link from their ABHA app.</Typography>}
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography variant="subtitle1" fontWeight={600}>Care contexts (shareable records)</Typography>
              {manage && <Button size="small" variant="contained" disabled={!selected.length}
                onClick={() => act(() => api.post(`/hip/patients/${overview.patient.id}/care-contexts/link`, { care_context_ids: selected }))}>
                Link selected to ABHA
              </Button>}
            </Stack>
            <Typography variant="body2" color="text.secondary" mb={1}>Only approved records whose FHIR bundle passed validation appear here.</Typography>
            <CareContextTable rows={overview.care_contexts} selected={selected} onSelect={manage ? setSelected : undefined} />
          </Paper>

          {overview.link_requests.length > 0 && (
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="subtitle1" fontWeight={600} mb={1}>Patient link requests (OTP)</Typography>
              {overview.link_requests.map((r) => (
                <Typography key={r.link_reference} variant="body2">
                  <Chip size="small" label={r.status} color={LINK_COLOR[r.status]} /> {r.abha_address} · {r.care_contexts.length} record(s) · expires {formatDateTime(r.expires_at)}
                  {r.test_otp && <> · <b>Test OTP {r.test_otp}</b> <ModeChip mode={r.mode} /></>}
                </Typography>
              ))}
            </Paper>
          )}

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={1}>Consents</Typography>
            <ConsentTable rows={overview.consents} />
          </Paper>

          {mode === 'simulation' && manage && <Simulator overview={overview} onDone={(text, ok = true) => { setMessage({ ok, text }); load(); onChange() }} />}
        </>
      )}
    </Stack>
  )
}

function CareContextTable({ rows, selected = [], onSelect }: { rows: CareContextRow[]; selected?: number[]; onSelect?: (ids: number[]) => void }) {
  if (!rows.length) return <Typography color="text.secondary">No shareable records yet.</Typography>
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            {onSelect && <TableCell />}
            <TableCell>Reference</TableCell><TableCell>Record</TableCell><TableCell>HI type</TableCell>
            <TableCell>Status</TableCell><TableCell>Linked to</TableCell><TableCell>Message</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((c) => (
            <TableRow key={c.id}>
              {onSelect && (
                <TableCell padding="checkbox">
                  <Checkbox size="small" disabled={c.link_status === 'linked'} checked={selected.includes(c.id)}
                    onChange={(e) => onSelect(e.target.checked ? [...selected, c.id] : selected.filter((x) => x !== c.id))} />
                </TableCell>
              )}
              <TableCell>{c.reference}</TableCell>
              <TableCell>{c.display}</TableCell>
              <TableCell>{c.hi_type}</TableCell>
              <TableCell><Chip size="small" label={c.link_status.replace('_', ' ')} color={LINK_COLOR[c.link_status]} /></TableCell>
              <TableCell>{c.linked_abha_address}{c.link_method && <Typography variant="caption" display="block">{c.link_method.replace('_', ' ')}</Typography>}</TableCell>
              <TableCell sx={{ maxWidth: 260 }}>{c.message}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  )
}

function ConsentTable({ rows }: { rows: HipConsentRow[] }) {
  if (!rows.length) return <Typography color="text.secondary">No consents received.</Typography>
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Table size="small">
        <TableHead>
          <TableRow><TableCell>Status</TableCell><TableCell>Patient</TableCell><TableCell>HIU</TableCell><TableCell>Types</TableCell>
            <TableCell>Records</TableCell><TableCell>Period</TableCell><TableCell>Erase at</TableCell><TableCell>Note</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {rows.map((c) => (
            <TableRow key={c.consent_id}>
              <TableCell><Chip size="small" label={c.status} color={LINK_COLOR[c.status]} /> <ModeChip mode={c.mode} /></TableCell>
              <TableCell>{c.patient?.full_name ?? c.abha_address}</TableCell>
              <TableCell>{c.hiu_id}<Typography variant="caption" display="block">{c.purpose}</Typography></TableCell>
              <TableCell>{c.hi_types.join(', ')}</TableCell>
              <TableCell>{c.care_contexts.length}</TableCell>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>{c.date_from ? `${formatDateTime(c.date_from)} – ${formatDateTime(c.date_to)}` : '—'}</TableCell>
              <TableCell>{formatDateTime(c.data_erase_at)}</TableCell>
              <TableCell sx={{ maxWidth: 240 }}>{c.status_reason}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  )
}

/** Simulated ABDM, ABHA app and HIU (simulation mode only), to exercise every HIP workflow end to end. */
function Simulator({ overview, onDone }: { overview: HipPatientOverview; onDone: (text: string, ok?: boolean) => void }) {
  const p = overview.patient
  const [abha, setAbha] = useState(p.abha_address ?? `${p.full_name.toLowerCase().replace(/[^a-z]/g, '.')}@sbx`)
  const [discovered, setDiscovered] = useState<{ transactionId: string; patient: any[] } | null>(null)
  const [link, setLink] = useState<{ reference: string; otp: string } | null>(null)
  const [consentId, setConsentId] = useState('')
  const [range, setRange] = useState({ from: '2020-01-01', to: new Date().toISOString().slice(0, 10) })
  const [result, setResult] = useState<any>(null)
  const call = async (fn: () => Promise<any>) => {
    try {
      await fn()
    } catch (e) {
      onDone(errorMessage(e), false)
    }
  }
  const active = overview.consents.filter((c) => c.status === 'GRANTED')

  return (
    <Paper variant="outlined" sx={{ p: 2, bgcolor: '#f3f8fc' }}>
      <Stack direction="row" spacing={1} alignItems="center" mb={1}>
        <Typography variant="subtitle1" fontWeight={600}>Simulator</Typography><ModeChip mode="simulation" />
      </Stack>
      <Typography variant="body2" color="text.secondary" mb={2}>
        Plays ABDM, the patient's ABHA app and a requesting provider (HIU). Messages use the real ABDM V3 formats and
        signed tokens, but nothing leaves this computer.
      </Typography>
      <Stack spacing={1.5}>
        <TextField label="Patient's ABHA address (in the simulated ABHA app)" value={abha} onChange={(e) => setAbha(e.target.value)} sx={{ maxWidth: 420 }} />
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} flexWrap="wrap" useFlexGap>
          <Button size="small" variant="outlined" onClick={() => call(async () => {
            await api.post('/hip/simulate/abha', { abha_address: abha, name: p.full_name, gender: p.gender, year_of_birth: p.year_of_birth })
            onDone(`Simulated ABHA account ${abha} created with this patient's name, gender and birth year`)
          })}>1. Create simulated ABHA account</Button>
          <Button size="small" variant="outlined" onClick={() => call(async () => {
            const { data } = await api.post(`/hip/simulate/discover/${p.id}`, { abha_address: abha })
            if (data.response?.error) return onDone(`Discovery: ${data.response.error.message}`, false)
            setDiscovered({ transactionId: data.response.transactionId, patient: data.response.patient })
            onDone(`Discovery found ${data.response.patient.reduce((n: number, g: any) => n + g.count, 0)} record(s), matched by ${data.response.matchedBy}`)
          })}>2. Patient discovers records</Button>
          <Button size="small" variant="outlined" disabled={!discovered} onClick={() => call(async () => {
            const { data } = await api.post('/hip/simulate/link-init', { transaction_id: discovered!.transactionId, abha_address: abha, patient: discovered!.patient })
            if (data.response?.error) return onDone(`Link: ${data.response.error.message}`, false)
            setLink({ reference: data.response.link.referenceNumber, otp: data.sms_to_patient })
            onDone(`OTP sent to ${data.response.link.meta.communicationHint} (simulated SMS: ${data.sms_to_patient})`)
          })}>3. Patient links (OTP sent)</Button>
          <Button size="small" variant="outlined" disabled={!link} onClick={() => call(async () => {
            const { data } = await api.post('/hip/simulate/link-confirm', { link_reference: link!.reference, otp: link!.otp })
            onDone(data.error ? `Confirm: ${data.error.message}` : 'Records linked to the ABHA address', !data.error)
          })}>4. Patient enters OTP</Button>
        </Stack>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} alignItems={{ md: 'center' }}>
          <TextField label="From" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} sx={{ width: 170 }} slotProps={{ inputLabel: { shrink: true } }} />
          <TextField label="To" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} sx={{ width: 170 }} slotProps={{ inputLabel: { shrink: true } }} />
          <Button size="small" variant="outlined" onClick={() => call(async () => {
            const erase = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 19)
            const { data } = await api.post('/hip/simulate/consent', { patient_id: p.id, date_from: range.from, date_to: range.to, erase_at: erase })
            setConsentId(data.consent_id)
            onDone(data.response?.error ? `Consent refused: ${data.response.error.message}` : 'Consent granted and verified', !data.response?.error)
          })}>5. Patient grants consent</Button>
          <TextField select label="Consent" value={consentId} onChange={(e) => setConsentId(e.target.value)} sx={{ minWidth: 220 }}>
            {active.map((c) => <MenuItem key={c.consent_id} value={c.consent_id}>{c.hiu_id} · {c.consent_id.slice(0, 8)}</MenuItem>)}
          </TextField>
          <Button size="small" variant="contained" disabled={!consentId} onClick={() => call(async () => {
            const { data } = await api.post('/hip/simulate/health-information', { consent_id: consentId, date_from: range.from, date_to: range.to })
            setResult(data)
            const s = data.notification?.notification.statusNotification
            onDone(data.on_request?.error ? `Request refused: ${data.on_request.error.message}` : `Transfer ${s?.sessionStatus}: ${data.received_by_hiu.length} record(s) decrypted by the HIU`,
              !data.on_request?.error && s?.sessionStatus === 'TRANSFERRED')
          })}>6. HIU requests data</Button>
        </Stack>
        {result && (
          <Table size="small">
            <TableHead><TableRow><TableCell>Care context</TableCell><TableCell>Status sent to ABDM</TableCell><TableCell>Received by HIU</TableCell></TableRow></TableHead>
            <TableBody>
              {(result.notification?.notification.statusNotification.statusResponses ?? []).map((s: any) => {
                const got = result.received_by_hiu.find((r: any) => r.careContextReference === s.careContextReference)
                return (
                  <TableRow key={s.careContextReference}>
                    <TableCell>{s.careContextReference}</TableCell>
                    <TableCell><Chip size="small" label={s.hiStatus} color={s.hiStatus === 'DELIVERED' ? 'success' : 'error'} /> {s.description}</TableCell>
                    <TableCell>{got ? `${got.profile} bundle (${got.entries} entries), checksum ${got.checksum_ok ? 'OK' : 'mismatch'}` : '—'}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Stack>
    </Paper>
  )
}

function CareContexts() {
  const [status, setStatus] = useState('')
  const { data, loading, error, pagination } = usePaged<CareContextRow>('/hip/care-contexts', { link_status: status })
  return (
    <>
      <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 200, mb: 2 }}>
        <MenuItem value="">All</MenuItem>
        {['not_linked', 'linking', 'linked', 'failed'].map((s) => <MenuItem key={s} value={s}>{s.replace('_', ' ')}</MenuItem>)}
      </TextField>
      {loading && <LinearProgress />}
      {error && <Alert severity="error">{error}</Alert>}
      <CareContextTable rows={data.items} />
      <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
    </>
  )
}

const REQUEST_VIEWS = [
  { label: 'Discovery requests', kind: 'discover', status: '' },
  { label: 'Pending data requests', kind: 'health_information', status: 'pending' },
  { label: 'Completed sharing', kind: 'health_information', status: 'success' },
  { label: 'Failed requests', kind: '', status: 'failed' },
]

function Requests() {
  const [view, setView] = useState(0)
  const [showConsents, setShowConsents] = useState(false)
  const consents = usePaged<HipConsentRow>('/hip/consents')
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {REQUEST_VIEWS.map((v, i) => <Chip key={v.label} label={v.label} color={i === view && !showConsents ? 'primary' : 'default'} onClick={() => { setView(i); setShowConsents(false) }} />)}
        <Chip label="Consents" color={showConsents ? 'primary' : 'default'} onClick={() => setShowConsents(true)} />
      </Stack>
      {showConsents ? <><ConsentTable rows={consents.data.items} /><TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...consents.pagination} /></>
        : <TransactionTable kind={REQUEST_VIEWS[view].kind} status={REQUEST_VIEWS[view].status} />}
    </Stack>
  )
}

function TransactionLog() {
  const [status, setStatus] = useState('')
  return (
    <>
      <TextField select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 200, mb: 2 }}>
        <MenuItem value="">All</MenuItem>
        {['pending', 'success', 'failed'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
      </TextField>
      <Typography variant="body2" color="text.secondary" mb={1}>Details are redacted: tokens, keys, OTPs and health record content are never stored in the log.</Typography>
      <TransactionTable kind="" status={status} />
    </>
  )
}

function TransactionTable({ kind, status }: { kind: string; status: string }) {
  const { data, loading, error, pagination } = usePaged<HipTransactionRow>('/hip/transactions', { kind, status })
  const [open, setOpen] = useState<number | null>(null)
  return (
    <>
      {loading && <LinearProgress />}
      {error && <Alert severity="error">{error}</Alert>}
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow><TableCell /><TableCell>Time</TableCell><TableCell>Exchange</TableCell><TableCell>Status</TableCell>
              <TableCell>Patient</TableCell><TableCell>What happened</TableCell></TableRow>
          </TableHead>
          <TableBody>
            {data.items.map((t) => (
              <Fragment key={t.id}>
                <TableRow hover>
                  <TableCell padding="checkbox"><IconButton size="small" onClick={() => setOpen(open === t.id ? null : t.id)}><ExpandMoreIcon fontSize="small" /></IconButton></TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(t.created_at)}</TableCell>
                  <TableCell>{KIND_LABEL[t.kind] ?? t.kind} <Typography variant="caption" display="block">{t.direction} · <ModeChip mode={t.mode} /></Typography></TableCell>
                  <TableCell><Chip size="small" label={t.status} color={STATUS_COLOR[t.status]} /></TableCell>
                  <TableCell>{t.patient?.full_name ?? '—'}</TableCell>
                  <TableCell sx={{ maxWidth: 420 }}>{t.message}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={6} sx={{ py: 0, borderBottom: open === t.id ? undefined : 'none' }}>
                    <Collapse in={open === t.id} unmountOnExit>
                      <Box component="pre" sx={{ fontSize: 12, m: 1, whiteSpace: 'pre-wrap' }}>
                        {JSON.stringify({ request_id: t.request_id, transaction_id: t.transaction_id, consent_id: t.consent_id, http_status: t.http_status, details: t.details }, null, 2)}
                      </Box>
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            ))}
            {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>Nothing yet.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Box>
      <TablePagination component="div" rowsPerPageOptions={[10, 25, 50]} {...pagination} />
    </>
  )
}

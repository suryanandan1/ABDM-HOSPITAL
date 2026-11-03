import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, FormGroup,
  Grid2 as Grid, LinearProgress, MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TablePagination,
  TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { api, Doctor, errorMessage, formatDate, formatDateTime, Page, PatientSummary } from '../api'
import { useAuth } from '../auth'
import { PatientBanner, PatientPicker } from '../components/clinical'
import { usePaged } from '../usePaged'
import { ModeChip } from './Hip'

interface HiuRequest {
  id: number; patient: PatientSummary; abha_address: string; purpose_code: string; hi_types: string[]; date_from: string
  date_to: string; data_erase_at: string; reason: string | null; status: string; status_message: string | null
  consent_request_id: string | null; mode: string; created_at: string
}
interface Suggestion { purpose_code: string; hi_types: string[]; date_from: string | null; date_to: string | null; missing_information: string[]; explanation: string }
interface SummaryItem { text: string; source_record_ids: number[] }

const STATUS_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  requested: 'info', pending_consent: 'warning', granted: 'info', retrieving: 'info', received: 'success', denied: 'error',
  revoked: 'default', expired: 'default', failed: 'error', valid: 'success', invalid: 'error', notified: 'info',
  acknowledged: 'info', not_validated: 'default', error: 'warning',
}
const HI_LABEL: Record<string, string> = {
  OPConsultation: 'OPD consultation', Prescription: 'Prescription', DiagnosticReport: 'Laboratory report',
  DischargeSummary: 'Discharge summary', ImmunizationRecord: 'Immunization', HealthDocumentRecord: 'Health document',
  WellnessRecord: 'Wellness record',
}
const StatusChip = ({ status }: { status: string }) => <Chip size="small" label={status.replace(/_/g, ' ')} color={STATUS_COLOR[status] ?? 'default'} />

export default function Hiu() {
  const { can } = useAuth()
  const [tab, setTab] = useState(0)
  const [dash, setDash] = useState<any>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const load = () => api.get('/hiu/dashboard').then((r) => setDash(r.data))
  useEffect(() => { load() }, [tab])
  const open = (id: number) => { setSelected(id); setTab(2) }

  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center" mb={2}>
        <Typography variant="h5">External records (ABDM HIU)</Typography>
        {dash && <ModeChip mode={dash.mode} />}
      </Stack>
      {dash?.mode === 'simulation' && (
        <Alert severity="info" sx={{ mb: 2 }}>Simulation: consent decisions and records come from a simulated consent manager and a
          simulated provider (Sunrise Clinic). They are synthetic and are not ABDM retrievals.</Alert>
      )}
      <Paper>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
          <Tab label="Dashboard" />
          {can('hiu:request') && <Tab label="New request" value={1} />}
          <Tab label="Request detail" value={2} disabled={selected === null} />
          {can('hiu:records') && <Tab label="Retrieved records" value={3} />}
        </Tabs>
        <Box sx={{ p: 2 }}>
          {tab === 0 && <Dashboard dash={dash} onOpen={open} />}
          {tab === 1 && <NewRequest purposes={dash?.purposes ?? {}} hiTypes={dash?.hi_types ?? []} onCreated={open} />}
          {tab === 2 && selected !== null && <RequestDetail id={selected} simulation={dash?.mode === 'simulation'} onChange={load} />}
          {tab === 3 && <RetrievedRecords />}
        </Box>
      </Paper>
    </>
  )
}

function Dashboard({ dash, onOpen }: { dash: any; onOpen: (id: number) => void }) {
  const { data, loading, pagination } = usePaged<HiuRequest>('/hiu/requests')
  if (!dash) return <LinearProgress />
  const c = dash.counts
  const tiles = [['New today', c.new_today], ['Pending consent', c.pending_consent], ['Consent granted', c.granted],
    ['Consent denied', c.denied], ['Retrieval in progress', c.retrieving], ['Records received', c.received],
    ['Failed / ended', c.failed], ['Stored records', c.records]] as const
  return (
    <Stack spacing={2}>
      <Grid container spacing={1.5}>
        {tiles.map(([label, value]) => (
          <Grid key={label} size={{ xs: 6, md: 3 }}>
            <Paper variant="outlined" sx={{ p: 1.5 }}><Typography variant="h6">{value ?? 0}</Typography>
              <Typography variant="body2" color="text.secondary">{label}</Typography></Paper>
          </Grid>
        ))}
      </Grid>
      <Typography variant="subtitle1" fontWeight={600}>Request history</Typography>
      {loading && <LinearProgress />}
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead><TableRow><TableCell>Created</TableCell><TableCell>Patient</TableCell><TableCell>Records requested</TableCell>
            <TableCell>Purpose</TableCell><TableCell>Status</TableCell><TableCell>What is happening</TableCell></TableRow></TableHead>
          <TableBody>
            {data.items.map((r) => (
              <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => onOpen(r.id)}>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(r.created_at)}</TableCell>
                <TableCell>{r.patient.full_name}<Typography variant="caption" display="block">{r.abha_address}</Typography></TableCell>
                <TableCell>{r.hi_types.map((t) => HI_LABEL[t]).join(', ')}</TableCell>
                <TableCell>{dash.purposes[r.purpose_code]}</TableCell>
                <TableCell><StatusChip status={r.status} /> <ModeChip mode={r.mode} /></TableCell>
                <TableCell sx={{ maxWidth: 360 }}>{r.status_message}</TableCell>
              </TableRow>
            ))}
            {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No requests yet.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Box>
      <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
    </Stack>
  )
}

function NewRequest({ purposes, hiTypes, onCreated }: { purposes: Record<string, string>; hiTypes: string[]; onCreated: (id: number) => void }) {
  const { user } = useAuth()
  const [patient, setPatient] = useState<PatientSummary | null>(null)
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const today = new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({
    requester_doctor_id: user?.doctor_id ?? '' as number | '', reason: '', purpose_code: 'CAREMGT', hi_types: [] as string[],
    date_from: '', date_to: today, erase_at: new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10),
  })
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [aiFields, setAiFields] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<Page<Doctor>>('/doctors', { params: { is_active: true, page_size: 100 } }).then((r) => setDoctors(r.data.items)).catch(() => undefined)
  }, [])
  const set = (key: keyof typeof form, value: any) => { setForm({ ...form, [key]: value }); setAiFields(aiFields.filter((f) => f !== key)) }
  const aiChip = (key: string) => aiFields.includes(key) && <Chip size="small" icon={<AutoAwesomeIcon />} label="AI suggested — please review" color="secondary" variant="outlined" />

  const suggest = async () => {
    setBusy(true)
    try {
      const { data } = await api.post<Suggestion>('/hiu/suggest', { patient_id: patient!.id, reason: form.reason })
      setSuggestion(data)
      setForm({ ...form, purpose_code: data.purpose_code, hi_types: data.hi_types, date_from: data.date_from ?? form.date_from, date_to: data.date_to ?? form.date_to })
      setAiFields(['purpose_code', 'hi_types', ...(data.date_from ? ['date_from'] : []), ...(data.date_to ? ['date_to'] : [])])
      setError('')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const submit = async () => {
    setBusy(true)
    try {
      const { data } = await api.post<HiuRequest>('/hiu/requests', {
        patient_id: patient!.id, requester_doctor_id: form.requester_doctor_id || null, purpose_code: form.purpose_code,
        hi_types: form.hi_types, date_from: form.date_from, date_to: form.date_to, erase_at: form.erase_at, reason: form.reason || null,
        ai_suggestion: suggestion ? { ...suggestion, accepted_unchanged: aiFields } : null,
      })
      onCreated(data.id)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const ready = patient?.abha_verified && form.hi_types.length && form.date_from && form.date_to && form.requester_doctor_id

  return (
    <Stack spacing={2} sx={{ maxWidth: 900 }}>
      <PatientPicker onChange={setPatient} />
      {patient && <PatientBanner patient={patient} />}
      {patient && !patient.abha_verified && <Alert severity="warning">This patient's ABHA address is not verified. Verify it on the ABDM HIP page before requesting records.</Alert>}
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <TextField select label="Requesting doctor" value={form.requester_doctor_id} onChange={(e) => set('requester_doctor_id', Number(e.target.value))}>
            {doctors.map((d) => <MenuItem key={d.id} value={d.id}>{d.full_name} ({d.registration_number})</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <TextField label="ABHA address (from the patient profile)" value={patient?.abha_address ?? ''} slotProps={{ input: { readOnly: true } }} />
        </Grid>
        <Grid size={12}>
          <TextField label="Clinical reason (why the records are needed)" multiline minRows={2} value={form.reason} onChange={(e) => set('reason', e.target.value)} />
        </Grid>
      </Grid>
      <Box><Button startIcon={<AutoAwesomeIcon />} variant="outlined" disabled={!patient || form.reason.trim().length < 3 || busy} onClick={suggest}>Suggest with AI</Button></Box>
      {suggestion && (
        <Alert severity="info" icon={<AutoAwesomeIcon />}>
          {suggestion.explanation}
          {suggestion.missing_information.length > 0 && <><br /><b>Check:</b> {suggestion.missing_information.join('; ')}</>}
          <br /><i>AI suggestions only. You decide what is requested; the patient decides on consent.</i>
        </Alert>
      )}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField select label="Purpose of request" value={form.purpose_code} onChange={(e) => set('purpose_code', e.target.value)} sx={{ maxWidth: 360 }}>
          {Object.entries(purposes).map(([k, v]) => <MenuItem key={k} value={k}>{v}</MenuItem>)}
        </TextField>
        {aiChip('purpose_code')}
      </Stack>
      <Box>
        <Stack direction="row" spacing={1} alignItems="center"><Typography variant="subtitle2">Record categories</Typography>{aiChip('hi_types')}</Stack>
        <FormGroup row>
          {hiTypes.map((t) => (
            <FormControlLabel key={t} label={HI_LABEL[t]} control={<Checkbox size="small" checked={form.hi_types.includes(t)}
              onChange={(e) => set('hi_types', e.target.checked ? [...form.hi_types, t] : form.hi_types.filter((x) => x !== t))} />} />
          ))}
        </FormGroup>
      </Box>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
        <TextField label="Records from" type="date" value={form.date_from} onChange={(e) => set('date_from', e.target.value)} sx={{ width: 180 }} slotProps={{ inputLabel: { shrink: true } }} />
        <TextField label="Records to" type="date" value={form.date_to} onChange={(e) => set('date_to', e.target.value)} sx={{ width: 180 }} slotProps={{ inputLabel: { shrink: true } }} />
        <TextField label="Delete received data on" type="date" value={form.erase_at} onChange={(e) => set('erase_at', e.target.value)} sx={{ width: 200 }} slotProps={{ inputLabel: { shrink: true } }} />
        {aiChip('date_from')}
      </Stack>
      <Box><Button variant="contained" disabled={!ready || busy} onClick={submit}>Send consent request to the patient</Button></Box>
    </Stack>
  )
}

function RequestDetail({ id, simulation, onChange }: { id: number; simulation: boolean; onChange: () => void }) {
  const { can } = useAuth()
  const [detail, setDetail] = useState<any>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const load = () => api.get(`/hiu/requests/${id}`).then((r) => setDetail(r.data))
  useEffect(() => { load() }, [id])
  const act = (request: () => Promise<any>, ok: string) => request()
    .then(() => setMessage({ ok: true, text: ok })).catch((e) => setMessage({ ok: false, text: errorMessage(e) }))
    .finally(() => { load(); onChange() })
  if (!detail) return <LinearProgress />
  const r: HiuRequest = detail.request
  const manage = can('hiu:request')

  return (
    <Stack spacing={2}>
      <PatientBanner patient={r.patient} />
      {message && <Alert severity={message.ok ? 'success' : 'error'} onClose={() => setMessage(null)}>{message.text}</Alert>}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" mb={1}><StatusChip status={r.status} /><ModeChip mode={r.mode} />
          <Typography>{r.status_message}</Typography></Stack>
        <Typography variant="body2">Requested by {detail.requester} · {r.hi_types.map((t) => HI_LABEL[t]).join(', ')} ·
          {' '}{formatDate(r.date_from)} – {formatDate(r.date_to)} · data deleted {formatDate(r.data_erase_at)}</Typography>
        {r.reason && <Typography variant="body2">Reason: {r.reason}</Typography>}
        <Stack direction="row" spacing={1} mt={1}>
          {manage && r.consent_request_id && <Button size="small" onClick={() => act(() => api.post(`/hiu/requests/${id}/check-status`), 'Status requested')}>Check consent status</Button>}
          {simulation && manage && r.status === 'pending_consent' && (
            <>
              <Button size="small" variant="outlined" color="success" onClick={() => act(() => api.post(`/hiu/simulate/requests/${id}/decide`, { grant: true }), 'Simulated patient granted consent')}>Simulate: patient grants</Button>
              {r.hi_types.length > 1 && <Button size="small" variant="outlined" onClick={() => act(() => api.post(`/hiu/simulate/requests/${id}/decide`, { grant: true, hi_types: [r.hi_types[0]] }), 'Simulated partial consent')}>Simulate: grants only {HI_LABEL[r.hi_types[0]]}</Button>}
              <Button size="small" variant="outlined" color="error" onClick={() => act(() => api.post(`/hiu/simulate/requests/${id}/decide`, { grant: false, reason: 'Declined by patient (simulated)' }), 'Simulated denial')}>Simulate: patient denies</Button>
            </>
          )}
          {simulation && manage && ['received', 'retrieving', 'granted'].includes(r.status) && (
            <Button size="small" color="error" onClick={() => act(() => api.post(`/hiu/simulate/requests/${id}/revoke`), 'Simulated revocation: data erased')}>Simulate: patient revokes</Button>
          )}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={1}>Consent artefacts</Typography>
        {detail.artefacts.length === 0 && <Typography color="text.secondary">None yet. Nothing can be retrieved until the patient grants consent.</Typography>}
        {detail.artefacts.map((a: any) => (
          <Stack key={a.id} direction={{ xs: 'column', md: 'row' }} spacing={1} alignItems={{ md: 'center' }} mb={1}>
            <StatusChip status={a.status} /><Typography variant="body2">{a.hip_id ?? 'provider'} · {(a.hi_types ?? []).map((t: string) => HI_LABEL[t]).join(', ')} · {a.care_contexts} record(s) · {a.status_message}</Typography>
            {manage && ['notified', 'invalid'].includes(a.status) && <Button size="small" onClick={() => act(() => api.post(`/hiu/artefacts/${a.id}/fetch`), 'Fetch requested')}>Fetch again</Button>}
            {manage && a.status === 'valid' && !detail.data_requests.some((d: any) => ['requested', 'acknowledged'].includes(d.status)) && (
              <Button size="small" onClick={() => act(() => api.post(`/hiu/artefacts/${a.id}/request-data`), 'Data requested again')}>Request data again</Button>
            )}
          </Stack>
        ))}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={1}>Data transfers</Typography>
        {detail.data_requests.map((d: any) => (
          <Typography key={d.id} variant="body2"><StatusChip status={d.status} /> pages {d.pages} · {d.status_message} · expires {formatDateTime(d.expires_at)}</Typography>
        ))}
        {detail.data_requests.length === 0 && <Typography color="text.secondary">No data requested yet.</Typography>}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={1}>Exchange timeline</Typography>
        <Table size="small"><TableBody>
          {detail.timeline.map((t: any, i: number) => (
            <TableRow key={i}><TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(t.at)}</TableCell><TableCell>{t.kind.replace('hiu_', '').replace(/_/g, ' ')} ({t.direction})</TableCell>
              <TableCell><StatusChip status={t.status === 'success' ? 'received' : t.status === 'failed' ? 'failed' : 'requested'} /></TableCell><TableCell>{t.message}</TableCell></TableRow>
          ))}
        </TableBody></Table>
      </Paper>
    </Stack>
  )
}

function RetrievedRecords() {
  const [patient, setPatient] = useState<PatientSummary | null>(null)
  const [view, setView] = useState<any>(null)
  const [opened, setOpened] = useState<any>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = (p = patient) => p && api.get(`/hiu/patients/${p.id}/records`).then((r) => setView(r.data)).catch((e) => setError(errorMessage(e)))
  useEffect(() => { load() }, [patient])
  const open = (id: number) => api.get(`/hiu/records/${id}`).then((r) => setOpened(r.data))
  const summarize = () => { setBusy(true); api.post(`/hiu/patients/${patient!.id}/summary`).then(() => load()).catch((e) => setError(errorMessage(e))).finally(() => setBusy(false)) }
  const sources = (ids: number[]) => ids.map((rid) => <Chip key={rid} size="small" label={`record #${rid}`} onClick={() => open(rid)} sx={{ ml: 0.5 }} />)

  return (
    <Stack spacing={2}>
      <PatientPicker onChange={setPatient} />
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      {view && (
        <>
          <PatientBanner patient={view.patient} />
          <Typography variant="body2" color="text.secondary">Records received from other providers under the patient's consent. Originals are kept unmodified and deleted on the consent's erase date. Every view is logged.</Typography>
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead><TableRow><TableCell>#</TableCell><TableCell>Source</TableCell><TableCell>Category</TableCell><TableCell>Record date</TableCell>
                <TableCell>Retrieved</TableCell><TableCell>Consent</TableCell><TableCell>Validation</TableCell><TableCell>Deleted on</TableCell></TableRow></TableHead>
              <TableBody>
                {view.records.map((r: any) => (
                  <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => open(r.id)}>
                    <TableCell>{r.id}</TableCell>
                    <TableCell>{r.source_name ?? r.source_hip_id} <ModeChip mode={r.mode} /></TableCell>
                    <TableCell>{r.category}</TableCell>
                    <TableCell>{formatDate(r.record_date)}</TableCell>
                    <TableCell>{formatDateTime(r.received_at)}</TableCell>
                    <TableCell>{r.consent_artefact_id.slice(0, 8)}…</TableCell>
                    <TableCell><StatusChip status={r.validation_status} />{!r.checksum_ok && <Chip size="small" label="checksum differs" color="warning" />}</TableCell>
                    <TableCell>{formatDate(r.erase_at)}</TableCell>
                  </TableRow>
                ))}
                {view.records.length === 0 && <TableRow><TableCell colSpan={8}>No retrieved records.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </Box>

          {view.records.length > 0 && (
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, lg: 6 }}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle1" fontWeight={600} mb={1}>Timeline</Typography>
                  {view.timeline.map((t: any) => (
                    <Box key={t.record_id} mb={1} sx={{ cursor: 'pointer' }} onClick={() => open(t.record_id)}>
                      <Typography variant="body2"><b>{formatDate(t.date)}</b> · {t.category} · {t.source}</Typography>
                      <Typography variant="caption" color="text.secondary">{t.highlights.join(' · ')}</Typography>
                    </Box>
                  ))}
                </Paper>
              </Grid>
              <Grid size={{ xs: 12, lg: 6 }}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle1" fontWeight={600} mb={1}>Repeated laboratory tests</Typography>
                  {view.lab_trends.length === 0 && <Typography color="text.secondary">No test appears more than once.</Typography>}
                  {view.lab_trends.map((t: any) => (
                    <Box key={t.test + t.loinc} mb={1.5}>
                      <Typography variant="body2"><b>{t.test}</b>{t.loinc && ` (LOINC ${t.loinc})`}{t.comparable ? ` · change ${t.change > 0 ? '+' : ''}${t.change} ${t.points[0].unit}` : ` · ${t.note}`}</Typography>
                      {t.points.map((p: any, i: number) => (
                        <Typography key={i} variant="caption" display="block" sx={{ cursor: 'pointer' }} onClick={() => open(p.record_id)}>
                          {formatDate(p.date)}: {p.value ?? p.value_text} {p.unit} {p.interpretation && `(${p.interpretation})`} {p.reference_range && `· ref ${p.reference_range}`} · record #{p.record_id}
                        </Typography>
                      ))}
                    </Box>
                  ))}
                </Paper>
              </Grid>
              <Grid size={12}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
                    <Typography variant="subtitle1" fontWeight={600}>AI summary</Typography>
                    <Button size="small" startIcon={<AutoAwesomeIcon />} disabled={busy} onClick={summarize}>{view.summary ? 'Regenerate' : 'Generate'} summary</Button>
                  </Stack>
                  {busy && <LinearProgress />}
                  {!view.summary && <Typography color="text.secondary">Not generated yet.</Typography>}
                  {view.summary && (
                    <>
                      <Alert severity={view.summary.outdated ? 'warning' : 'info'} sx={{ mb: 1 }}>
                        Generated by {view.summary.model} on {formatDateTime(view.summary.generated_at)} from the records below only; each statement links to its source.
                        {view.summary.outdated && ' New records arrived since: regenerate.'} {view.summary.removed_unsupported_items > 0 && `${view.summary.removed_unsupported_items} statement(s) without valid sources were removed.`}
                      </Alert>
                      {(['overview', 'consultations', 'prescriptions', 'laboratory'] as const).map((section) => view.summary[section]?.length > 0 && (
                        <Box key={section} mb={1}>
                          <Typography variant="subtitle2" sx={{ textTransform: 'capitalize' }}>{section}</Typography>
                          {view.summary[section].map((item: SummaryItem, i: number) => (
                            <Typography key={i} variant="body2">• {item.text}{sources(item.source_record_ids)}</Typography>
                          ))}
                        </Box>
                      ))}
                    </>
                  )}
                </Paper>
              </Grid>
            </Grid>
          )}
        </>
      )}

      <Dialog open={!!opened} onClose={() => setOpened(null)} maxWidth="md" fullWidth>
        {opened && (
          <>
            <DialogTitle>{opened.facts.category} · {opened.record.source_name} · {formatDate(opened.record.record_date)}</DialogTitle>
            <DialogContent>
              <Stack spacing={1}>
                {opened.facts.conditions.length > 0 && <Typography variant="body2"><b>Conditions:</b> {opened.facts.conditions.map((c: any) => c.text).join(', ')}</Typography>}
                {opened.facts.medications.length > 0 && <Typography variant="body2"><b>Medicines:</b> {opened.facts.medications.map((m: any) => `${m.name} (${m.dosage ?? ''})`).join('; ')}</Typography>}
                {opened.facts.observations.length > 0 && <Typography variant="body2"><b>Results:</b> {opened.facts.observations.map((o: any) => `${o.name} ${o.value ?? o.value_text ?? ''} ${o.unit ?? ''}`).join('; ')}</Typography>}
                {opened.facts.reports.map((r: any, i: number) => r.conclusion && <Typography key={i} variant="body2"><b>{r.name}:</b> {r.conclusion}</Typography>)}
                {opened.facts.sections.map((s: any, i: number) => <Typography key={i} variant="body2"><b>{s.title}:</b> {s.text}</Typography>)}
                <Typography variant="caption" color="text.secondary">Consent {opened.record.consent_artefact_id} · care context {opened.record.care_context_reference} · validation {opened.record.validation_status}</Typography>
                <Typography variant="subtitle2" mt={1}>Original FHIR bundle (as received)</Typography>
                <Box component="pre" sx={{ fontSize: 11, bgcolor: '#f4f6f9', p: 1.5, borderRadius: 1, overflow: 'auto', maxHeight: '45vh', m: 0 }}>{JSON.stringify(opened.original, null, 2)}</Box>
              </Stack>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => api.post(`/hiu/records/${opened.record.id}/validate`).then(() => open(opened.record.id))}>Validate (HL7)</Button>
              <Button onClick={() => setOpened(null)}>Close</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Stack>
  )
}

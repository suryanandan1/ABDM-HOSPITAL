import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, FormControlLabel, LinearProgress, MenuItem, Paper, Skeleton, Stack, Switch, Tab, Table,
  TableBody, TableCell, TableHead, TablePagination, TableRow, Tabs, TextField, Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import { api, errorMessage, FhirRecord, formatDateTime, ValidationIssue } from '../api'
import { useAuth } from '../auth'
import { PatientBanner } from '../components/clinical'
import { usePaged } from '../usePaged'

const VALIDATION = {
  not_validated: { label: 'Not validated', color: 'default' },
  valid: { label: 'Valid', color: 'success' },
  invalid: { label: 'Invalid', color: 'error' },
  error: { label: 'Validator error', color: 'warning' },
} as const

const SOURCE_PATH = { consultation: '/consultations', prescription: '/prescriptions', lab_order: '/lab/orders', document: '/documents' }

const CATEGORY_LABEL: Record<string, string> = {
  required: 'Missing required data', terminology: 'Terminology', reference: 'Resource reference', profile: 'Profile rule',
  structure: 'Bundle structure', data: 'Source data', validator: 'Validator', other: 'Other',
}

function ValidationChip({ record }: { record: FhirRecord }) {
  const v = VALIDATION[record.validation_status]
  return <Chip size="small" label={v.label} color={v.color} />
}

export default function FhirRecords() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const { data, loading, error, pagination } = usePaged<FhirRecord>('/fhir/records', { validation_status: status })
  return (
    <>
      <Typography variant="h5" mb={2}>FHIR records</Typography>
      <Paper>
        <Stack direction="row" sx={{ p: 2 }}>
          <TextField select label="Validation" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ width: 220 }}>
            <MenuItem value="">All</MenuItem>
            {Object.entries(VALIDATION).map(([k, v]) => <MenuItem key={k} value={k}>{v.label}</MenuItem>)}
          </TextField>
        </Stack>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Generated</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Record</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Validation</TableCell>
                <TableCell>HIP</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((r) => (
                <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/fhir/records/${r.id}`)}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(r.generated_at)} · v{r.version}</TableCell>
                  <TableCell>{r.patient.full_name}<br /><Typography variant="caption">{r.patient.patient_code}</Typography></TableCell>
                  <TableCell>{r.record_type}<br /><Typography variant="caption">{r.title}</Typography></TableCell>
                  <TableCell>{r.source_type.replace('_', ' ')} #{r.source_id}</TableCell>
                  <TableCell><ValidationChip record={r} /></TableCell>
                  <TableCell>{r.hip_eligible ? <Chip size="small" color="success" label="Eligible" /> : '—'}</TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No FHIR records yet. Approve a document or use “FHIR preview” on an approved record.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>
    </>
  )
}

function IssueTable({ issues, showInfo }: { issues: ValidationIssue[]; showInfo: boolean }) {
  const shown = issues.filter((i) => showInfo || i.severity !== 'information')
  if (shown.length === 0) return <Typography color="text.secondary">No issues.</Typography>
  return (
    <Table size="small">
      <TableHead>
        <TableRow><TableCell>Severity</TableCell><TableCell>Type</TableCell><TableCell>Where</TableCell><TableCell>What to fix</TableCell></TableRow>
      </TableHead>
      <TableBody>
        {shown.map((i, n) => (
          <TableRow key={n}>
            <TableCell><Chip size="small" label={i.severity} color={i.severity === 'error' ? 'error' : i.severity === 'warning' ? 'warning' : 'default'} /></TableCell>
            <TableCell>{CATEGORY_LABEL[i.category] ?? i.category}</TableCell>
            <TableCell sx={{ maxWidth: 260, wordBreak: 'break-word' }}>{i.location ?? '—'}</TableCell>
            <TableCell sx={{ wordBreak: 'break-word' }}>{i.message}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function FhirRecordPage() {
  const { id } = useParams()
  const { can } = useAuth()
  const navigate = useNavigate()
  const [record, setRecord] = useState<FhirRecord | null>(null)
  const [tab, setTab] = useState(0)
  const [busy, setBusy] = useState<'' | 'validate' | 'regenerate'>('')
  const [showInfo, setShowInfo] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<FhirRecord>(`/fhir/records/${id}`).then((r) => setRecord(r.data)).catch((e) => setError(errorMessage(e)))
  }, [id])

  if (!record) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={400} />

  const act = async (kind: 'validate' | 'regenerate') => {
    setBusy(kind)
    try {
      setRecord((await api.post<FhirRecord>(`/fhir/records/${id}/${kind}`)).data)
      setError('')
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy('')
    }
  }
  const download = async () => {
    const { data } = await api.get(`/fhir/records/${id}/download`, { responseType: 'blob' })
    const link = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(data), download: `${record.record_type}-${record.source_type}-${record.source_id}-v${record.version}.json`,
    })
    link.click()
    URL.revokeObjectURL(link.href)
  }
  const entries = record.bundle?.entry ?? []
  const errors = (record.validation_issues ?? []).filter((i) => i.severity === 'error').length
  const precheckErrors = (record.prechecks ?? []).filter((i) => i.severity === 'error').length

  return (
    <>
      <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" alignItems={{ md: 'center' }} mb={2} spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="h5">{record.record_type}</Typography>
          <Chip size="small" label={`v${record.version}`} variant="outlined" />
          <ValidationChip record={record} />
          {record.hip_eligible && <Chip size="small" color="success" label="Eligible for HIP sharing" />}
        </Stack>
        <Stack direction="row" spacing={1}>
          <Button onClick={() => navigate(`${SOURCE_PATH[record.source_type]}/${record.source_id}`)}>Open source record</Button>
          {can('fhir:generate') && <Button disabled={!!busy} onClick={() => act('regenerate')}>Regenerate from source</Button>}
          <Button startIcon={<DownloadIcon />} onClick={download}>Download JSON</Button>
          {can('fhir:generate') && <Button variant="contained" disabled={!!busy} onClick={() => act('validate')}>Validate</Button>}
        </Stack>
      </Stack>
      {busy === 'validate' && <Alert severity="info" sx={{ mb: 2 }}>Running the HL7 FHIR validator with the ABDM implementation guide. This takes about a minute…<LinearProgress sx={{ mt: 1 }} /></Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
      <PatientBanner patient={record.patient} />

      <Paper>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
          <Tab label={`Validation${errors + precheckErrors ? ` (${errors + precheckErrors} errors)` : ''}`} />
          <Tab label={`Resources (${entries.length})`} />
          <Tab label="JSON" />
        </Tabs>
        <Box sx={{ p: 2 }}>
          {tab === 0 && (
            <Stack spacing={3}>
              <Box>
                <Typography variant="subtitle1" fontWeight={600}>Local pre-checks</Typography>
                <Typography variant="body2" color="text.secondary" mb={1}>
                  Quick structural, reference and required-data checks done when the bundle was generated. These are not profile validation.
                </Typography>
                <IssueTable issues={record.prechecks ?? []} showInfo />
              </Box>
              <Box>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Typography variant="subtitle1" fontWeight={600}>FHIR R4 and ABDM profile validation</Typography>
                  <FormControlLabel control={<Switch checked={showInfo} onChange={(e) => setShowInfo(e.target.checked)} />} label="Show information messages" />
                </Stack>
                {record.validation_status === 'not_validated' ? (
                  <Alert severity="info">This version has not been validated yet. Run “Validate” to check it with the official HL7 FHIR validator.</Alert>
                ) : (
                  <>
                    <Typography variant="body2" color="text.secondary" mb={1}>{record.validator} · {formatDateTime(record.validated_at)}</Typography>
                    {record.validation_status === 'invalid' && (
                      <Alert severity="error" sx={{ mb: 1 }}>Fix the source record (or its review), then use “Regenerate from source” and validate again.</Alert>
                    )}
                    <IssueTable issues={record.validation_issues ?? []} showInfo={showInfo} />
                  </>
                )}
              </Box>
            </Stack>
          )}
          {tab === 1 && (
            <Table size="small">
              <TableHead><TableRow><TableCell>#</TableCell><TableCell>Resource</TableCell><TableCell>Profile</TableCell><TableCell>Summary</TableCell></TableRow></TableHead>
              <TableBody>
                {entries.map((e, n) => (
                  <TableRow key={e.fullUrl}>
                    <TableCell>{n}</TableCell>
                    <TableCell>{e.resource.resourceType}</TableCell>
                    <TableCell>{e.resource.meta?.profile?.[0]?.split('/').pop()}</TableCell>
                    <TableCell>{(e.resource.text?.div ?? '').replace(/<[^>]+>/g, ' ').trim().slice(0, 160)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {tab === 2 && (
            <Box component="pre" sx={{ fontSize: 12, bgcolor: '#f4f6f9', p: 2, borderRadius: 1, overflow: 'auto', maxHeight: '70vh', m: 0 }}>
              {JSON.stringify(record.bundle, null, 2)}
            </Box>
          )}
        </Box>
      </Paper>
    </>
  )
}

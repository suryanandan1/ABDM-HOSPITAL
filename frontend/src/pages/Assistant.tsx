import { useEffect, useState } from 'react'
import { Link as RouterLink, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Grid2 as Grid, LinearProgress, Link, Paper, Skeleton, Stack, TextField, Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { api, errorMessage, formatDate, Patient } from '../api'
import { PatientBanner } from '../components/clinical'

type Sources = Record<string, { type: string; date: string | null; source: string; link: string }>
interface Statement { text: string; kind: 'documented_fact' | 'ai_interpretation'; source_ids: string[]; dates: string[] }
interface Line { text: string; source_id: string; date: string | null; [key: string]: unknown }

const QUESTIONS = ['What were the previous laboratory reports?', 'What medicines were previously prescribed?',
  'Show the latest recorded consultation.', 'Summarize the available medical history.']

export function SourceChip({ id, sources }: { id: string; sources: Sources }) {
  const s = sources[id]
  const label = `${id}${s?.date ? ` · ${formatDate(s.date)}` : ''}`
  return s?.link ? <Chip size="small" label={label} component={RouterLink} to={s.link} clickable sx={{ ml: 0.5 }} />
    : <Chip size="small" label={label} sx={{ ml: 0.5 }} />
}

function StatementLine({ s, sources }: { s: Statement; sources: Sources }) {
  return (
    <Typography variant="body2" sx={{ mb: 0.5 }}>
      <Chip size="small" label={s.kind === 'documented_fact' ? 'Documented' : 'AI interpretation'}
        color={s.kind === 'documented_fact' ? 'success' : 'warning'} variant="outlined" sx={{ mr: 0.5 }} />
      {s.text}{s.source_ids.map((id) => <SourceChip key={id} id={id} sources={sources} />)}
    </Typography>
  )
}

/** Briefing shown on the patient profile: documented history only, no AI, every line cited. */
export function Briefing({ patientId }: { patientId: number }) {
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.get(`/assistant/patients/${patientId}/overview`).then((r) => setData(r.data)).catch((e) => setError(errorMessage(e)))
  }, [patientId])
  if (error) return <Alert severity="warning">{error}</Alert>
  if (!data) return <Skeleton variant="rounded" height={120} />
  const b = data.briefing
  const section = (title: string, lines: Line[], render: (l: Line) => string = (l) => l.text) => (
    <Box mb={1.5}>
      <Typography variant="subtitle2">{title}</Typography>
      {lines.length === 0 ? <Typography variant="body2" color="text.secondary">None recorded.</Typography> : lines.map((l, i) => (
        <Typography key={i} variant="body2">{render(l)}<SourceChip id={l.source_id} sources={data.sources} /></Typography>
      ))}
    </Box>
  )
  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="h6">Assistant briefing</Typography>
        <Button component={RouterLink} to={`/assistant/${patientId}`} startIcon={<AutoAwesomeIcon />}>Open assistant</Button>
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" mb={1.5}>
        Documented history you can access ({b.scope.join(', ')}). Not a diagnosis.
      </Typography>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          {section('Active conditions', b.active_conditions, (l) => `${l.text}${l.certainty !== 'confirmed' ? ` (${l.certainty})` : ''}`)}
          {section('Recent consultations', b.recent_consultations, (l) => `${formatDate(l.date)}: ${l.text}${(l.diagnoses as string[]).length ? ` — ${(l.diagnoses as string[]).join(', ')}` : ''}`)}
          {section('Previous prescriptions', b.previous_prescriptions, (l) => `${formatDate(l.date)}: ${l.text}`)}
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          {section('Recent laboratory results', b.recent_lab_results, (l) => `${formatDate(l.date)}: ${l.text}`)}
          {section('External records', b.external_records, (l) => `${formatDate(l.date)}: ${l.text}`)}
          {b.missing_information.length > 0 && <Alert severity="info">Missing: {b.missing_information.join('; ')}</Alert>}
        </Grid>
      </Grid>
    </Paper>
  )
}

export default function Assistant() {
  const { patientId } = useParams()
  const [patient, setPatient] = useState<Patient | null>(null)
  const [overview, setOverview] = useState<any>(null)
  const [summary, setSummary] = useState<any>(null)
  const [question, setQuestion] = useState('')
  const [answers, setAnswers] = useState<{ question: string; answer: any }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<Patient>(`/patients/${patientId}`).then((r) => setPatient(r.data)).catch((e) => setError(errorMessage(e)))
    api.get(`/assistant/patients/${patientId}/overview`).then((r) => setOverview(r.data)).catch((e) => setError(errorMessage(e)))
  }, [patientId])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    try { await fn(); setError('') } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const ask = (q: string) => run(async () => {
    const { data } = await api.post(`/assistant/patients/${patientId}/ask`, { question: q })
    setAnswers([{ question: q, answer: data }, ...answers])
    setQuestion('')
  })

  if (!patient || !overview) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />
  const sources: Sources = { ...overview.sources, ...(summary?.sources ?? {}) }

  return (
    <>
      <Typography variant="h5" mb={2}>Patient assistant</Typography>
      <PatientBanner patient={patient} />
      <Alert severity="info" sx={{ mb: 2 }}>
        Reads only records you are allowed to see. It does not diagnose, prescribe or change records; every statement links to its source.
      </Alert>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
      {busy && <LinearProgress sx={{ mb: 1 }} />}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 7 }}>
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Ask about this patient's records</Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                <TextField label="Question" value={question} onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && question.trim().length > 2 && ask(question)} />
                <Button variant="contained" disabled={busy || question.trim().length < 3} onClick={() => ask(question)}>Ask</Button>
              </Stack>
              <Stack direction="row" flexWrap="wrap" gap={1} mt={1}>
                {QUESTIONS.map((q) => <Chip key={q} label={q} size="small" onClick={() => ask(q)} />)}
              </Stack>
              {answers.map(({ question: q, answer }, i) => (
                <Box key={i} mt={2}>
                  <Typography variant="subtitle2">{q}</Typography>
                  {answer.information_unavailable && <Alert severity="warning" sx={{ my: 0.5 }}>Not available in the records: {answer.unavailable_reason}</Alert>}
                  {answer.statements.map((s: Statement, j: number) => <StatementLine key={j} s={s} sources={{ ...sources, ...answer.sources }} />)}
                  {answer.removed_unsupported > 0 && <Typography variant="caption" color="text.secondary">{answer.removed_unsupported} statement(s) without valid sources were removed.</Typography>}
                </Box>
              ))}
            </Paper>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
                <Typography variant="h6">Medical history summary</Typography>
                <Button startIcon={<AutoAwesomeIcon />} disabled={busy} onClick={() => run(async () => setSummary((await api.post(`/assistant/patients/${patientId}/summary`)).data))}>
                  {summary ? 'Regenerate' : 'Generate'}
                </Button>
              </Stack>
              {!summary && <Typography color="text.secondary">Generate an AI summary of the records you can access.</Typography>}
              {summary && (
                <>
                  {(['consultations', 'conditions', 'medications', 'laboratory', 'observations', 'external_records'] as const).map((k) => summary[k].length > 0 && (
                    <Box key={k} mb={1}>
                      <Typography variant="subtitle2" sx={{ textTransform: 'capitalize' }}>{k.replace('_', ' ')}</Typography>
                      {summary[k].map((s: Statement, j: number) => <StatementLine key={j} s={s} sources={sources} />)}
                    </Box>
                  ))}
                  {summary.missing_information.length > 0 && <Alert severity="info">Missing information: {summary.missing_information.join('; ')}</Alert>}
                  <Typography variant="caption" color="text.secondary">
                    Based on {summary.records_considered} record(s){summary.records_omitted ? `; ${summary.records_omitted} older record(s) not included` : ''}.
                    {summary.removed_unsupported > 0 && ` ${summary.removed_unsupported} unsupported statement(s) removed.`}
                  </Typography>
                </>
              )}
            </Paper>
          </Stack>
        </Grid>
        <Grid size={{ xs: 12, lg: 5 }}>
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Timeline</Typography>
              {overview.timeline.length === 0 && <Typography color="text.secondary">No records you can access.</Typography>}
              {overview.timeline.map((t: any) => (
                <Typography key={t.id} variant="body2" mb={0.5}>
                  <b>{formatDate(t.date)}</b> · <Link component={RouterLink} to={t.link}>{t.type}</Link> · {t.source}{t.author ? ` · ${t.author}` : ''}
                </Typography>
              ))}
            </Paper>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" mb={1}>Laboratory results over time</Typography>
              {overview.lab_trends.length === 0 && <Typography color="text.secondary">No test appears more than once.</Typography>}
              {overview.lab_trends.map((t: any) => (
                <Box key={t.test} mb={1}>
                  <Typography variant="body2"><b>{t.test}</b>{t.comparable ? ` · change ${t.change > 0 ? '+' : ''}${t.change} ${t.points[0].unit ?? ''}` : ` · ${t.note}`}</Typography>
                  {t.points.map((p: any, i: number) => (
                    <Typography key={i} variant="caption" display="block">{formatDate(p.date)}: {p.value ?? p.value_text} {p.unit} {p.interpretation && `(${p.interpretation})`}<SourceChip id={String(p.record_id)} sources={sources} /></Typography>
                  ))}
                </Box>
              ))}
            </Paper>
          </Stack>
        </Grid>
      </Grid>
    </>
  )
}

import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Grid2 as Grid,
  IconButton, Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import { api, errorMessage, formatRange, LabParameter, LabTest, LoincMapping } from '../api'
import { useAuth } from '../auth'

const EMPTY_PARAMETER: LabParameter = { code: '', name: '', unit: null, loinc_code: null, ref_low: null, ref_high: null, ref_text: null }

export default function LabTests() {
  const { can } = useAuth()
  const [tests, setTests] = useState<LabTest[]>([])
  const [editing, setEditing] = useState<LabTest | 'new' | null>(null)
  const [error, setError] = useState('')
  const canEdit = can('lab:catalog')

  const load = () => api.get<LabTest[]>('/lab/tests').then((r) => setTests(r.data)).catch((e) => setError(errorMessage(e)))
  useEffect(() => { load() }, [])

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Laboratory test catalogue</Typography>
        {canEdit && <Button variant="contained" startIcon={<AddIcon />} onClick={() => setEditing('new')}>Add test</Button>}
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper>
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Code</TableCell>
                <TableCell>Test</TableCell>
                <TableCell>Category</TableCell>
                <TableCell>Sample</TableCell>
                <TableCell>Parameters (unit, reference range)</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {tests.map((t) => (
                <TableRow key={t.id} hover={canEdit} sx={{ cursor: canEdit ? 'pointer' : 'default' }} onClick={() => canEdit && setEditing(t)}>
                  <TableCell>{t.code}</TableCell>
                  <TableCell>{t.name}{t.loinc_code && <Typography variant="caption" display="block">LOINC {t.loinc_code}</Typography>}</TableCell>
                  <TableCell>{t.category}</TableCell>
                  <TableCell>{t.sample_type}</TableCell>
                  <TableCell>
                    {t.parameters.map((p) => (
                      <Typography key={p.code} variant="body2">{p.name}{p.unit && ` (${p.unit})`}: {formatRange(p) || '—'}</Typography>
                    ))}
                  </TableCell>
                  <TableCell><Chip size="small" label={t.is_active ? 'Active' : 'Inactive'} color={t.is_active ? 'success' : 'default'} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </Paper>
      {editing && <TestDialog test={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}
    </>
  )
}

/** Suggest a LOINC code for a test name with the LOINC mapping project; applied only when the user clicks "Use". */
function LoincSuggestion({ name, onUse }: { name: string; onUse: (code: string) => void }) {
  const [result, setResult] = useState<LoincMapping | string | null>(null)
  const suggest = () => api.get<LoincMapping>('/terminology/loinc/suggest', { params: { q: name } })
    .then((r) => setResult(r.data.code ? r.data : 'No sufficiently similar LOINC term found'))
    .catch((e) => setResult(errorMessage(e)))
  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={{ minHeight: 28 }}>
      <Button size="small" disabled={name.trim().length < 2} onClick={suggest}>Suggest LOINC</Button>
      {typeof result === 'string' && <Typography variant="caption" color="text.secondary">{result}</Typography>}
      {result && typeof result !== 'string' && (
        <>
          <Typography variant="caption"><b>{result.code}</b> {result.display} · matched “{result.matched_term}” · {Math.round((result.confidence ?? 0) * 100)}%</Typography>
          <Button size="small" onClick={() => { onUse(result.code!); setResult(null) }}>Use</Button>
        </>
      )}
    </Stack>
  )
}

function TestDialog({ test, onClose, onSaved }: { test: LabTest | null; onClose: () => void; onSaved: () => void }) {
  const { can } = useAuth()
  const [form, setForm] = useState({
    code: test?.code ?? '', name: test?.name ?? '', category: test?.category ?? '', sample_type: test?.sample_type ?? '',
    loinc_code: test?.loinc_code ?? '', is_active: test?.is_active ?? true,
  })
  const [parameters, setParameters] = useState<LabParameter[]>(test?.parameters ?? [{ ...EMPTY_PARAMETER }])
  const [error, setError] = useState('')

  const setParam = (i: number, change: Partial<LabParameter>) => setParameters(parameters.map((p, j) => (j === i ? { ...p, ...change } : p)))
  const num = (v: string) => (v === '' ? null : Number(v))
  const save = async () => {
    try {
      if (test) await api.put(`/lab/tests/${test.id}`, { ...form, parameters })
      else await api.post('/lab/tests', { ...form, parameters })
      onSaved()
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const field = (name: keyof Omit<typeof form, 'is_active'>, label: string, size = 6) => (
    <Grid size={{ xs: 12, sm: size }}>
      <TextField label={label} value={form[name]} onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
    </Grid>
  )

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{test ? 'Edit test' : 'Add test'}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2} pt={1}>
          {field('code', 'Code', 3)}
          {field('name', 'Test name', 9)}
          {field('category', 'Category', 4)}
          {field('sample_type', 'Sample type', 4)}
          {field('loinc_code', 'LOINC code', 4)}
        </Grid>
        <Typography variant="subtitle1" fontWeight={600} mt={3} mb={1}>Parameters</Typography>
        <Typography variant="body2" color="text.secondary" mb={2}>
          Numeric results are flagged against low/high. Use reference text (e.g. Negative) for non-numeric results.
          Edits apply to new results only; existing reports keep the values they were issued with.
        </Typography>
        <Stack spacing={1.5}>
          {parameters.map((p, i) => (
            <Grid container spacing={1} key={i}>
              <Grid size={{ xs: 4, sm: 1.5 }}><TextField label="Code" value={p.code} onChange={(e) => setParam(i, { code: e.target.value })} /></Grid>
              <Grid size={{ xs: 8, sm: 3 }}><TextField label="Name" value={p.name} onChange={(e) => setParam(i, { name: e.target.value })} /></Grid>
              <Grid size={{ xs: 4, sm: 1.5 }}><TextField label="Unit" value={p.unit ?? ''} onChange={(e) => setParam(i, { unit: e.target.value || null })} /></Grid>
              <Grid size={{ xs: 4, sm: 1.25 }}><TextField label="Low" type="number" value={p.ref_low ?? ''} onChange={(e) => setParam(i, { ref_low: num(e.target.value) })} /></Grid>
              <Grid size={{ xs: 4, sm: 1.25 }}><TextField label="High" type="number" value={p.ref_high ?? ''} onChange={(e) => setParam(i, { ref_high: num(e.target.value) })} /></Grid>
              <Grid size={{ xs: 5, sm: 1.5 }}><TextField label="Ref. text" value={p.ref_text ?? ''} onChange={(e) => setParam(i, { ref_text: e.target.value || null })} /></Grid>
              <Grid size={{ xs: 5, sm: 1.5 }}><TextField label="LOINC" value={p.loinc_code ?? ''} onChange={(e) => setParam(i, { loinc_code: e.target.value || null })} /></Grid>
              <Grid size={{ xs: 2, sm: 0.5 }}>
                <IconButton disabled={parameters.length === 1} onClick={() => setParameters(parameters.filter((_, j) => j !== i))}><DeleteIcon /></IconButton>
              </Grid>
              {can('terminology:confirm') && (
                <Grid size={12}><LoincSuggestion name={p.name} onUse={(code) => setParam(i, { loinc_code: code })} /></Grid>
              )}
            </Grid>
          ))}
          <Box><Button startIcon={<AddIcon />} onClick={() => setParameters([...parameters, { ...EMPTY_PARAMETER }])}>Add parameter</Button></Box>
        </Stack>
        {test && (
          <FormControlLabel sx={{ mt: 2 }} label="Active (can be ordered)"
            control={<Switch checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />} />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  )
}

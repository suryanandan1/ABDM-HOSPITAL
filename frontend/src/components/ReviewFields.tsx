import { Box, Button, Chip, Grid2 as Grid, IconButton, InputAdornment, Paper, Stack, TextField, Tooltip, Typography } from '@mui/material'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import UndoIcon from '@mui/icons-material/Undo'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import { FieldGroup, ReviewField } from '../api'

export const humanize = (key: string) => (key[0].toUpperCase() + key.slice(1)).replace(/_/g, ' ').replace('icd10', 'ICD-10')

export const blankField = (): ReviewField => ({
  value: null, confidence: null, evidence: null, source: 'manual', status: 'missing', review: 'pending',
})

/** Where a value came from and what staff decided, shown next to every field. */
function Provenance({ field }: { field: ReviewField }) {
  if (field.review === 'rejected') return <Chip size="small" label="Rejected" variant="outlined" />
  if (field.value === null) return <Chip size="small" label="Missing" variant="outlined" />
  if (field.source === 'manual') return <Chip size="small" label="Manual" color="info" variant="outlined" />
  const pct = Math.round((field.confidence ?? 0) * 100)
  const label = field.review === 'accepted' ? `AI ${pct}% ✓` : `AI ${pct}%`
  return <Chip size="small" label={label} color={field.status === 'uncertain' ? 'warning' : 'success'}
    variant={field.review === 'accepted' ? 'filled' : 'outlined'} />
}

export function ReviewFieldInput({ label, field, onChange, readOnly, multiline }: {
  label: string; field: ReviewField; onChange: (f: ReviewField) => void; readOnly?: boolean; multiline?: boolean
}) {
  const needsReview = field.status === 'uncertain' && field.review === 'pending'
  const isAiPending = field.source === 'ai' && field.value !== null && field.review === 'pending'
  return (
    <TextField
      label={label}
      value={field.value ?? ''}
      multiline={multiline}
      color={needsReview ? 'warning' : undefined}
      focused={needsReview || undefined}
      disabled={field.review === 'rejected'}
      helperText={field.evidence ? `“${field.evidence}”` : needsReview ? 'Low confidence: please check' : ' '}
      onChange={(e) => onChange({ ...field, value: e.target.value || null, source: 'manual', review: 'edited', confidence: null, evidence: null })}
      slotProps={{
        input: {
          readOnly,
          endAdornment: (
            <InputAdornment position="end">
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Provenance field={field} />
                {!readOnly && isAiPending && (
                  <>
                    <Tooltip title="Accept suggestion"><IconButton size="small" color="success" onClick={() => onChange({ ...field, review: 'accepted' })}><CheckIcon fontSize="small" /></IconButton></Tooltip>
                    <Tooltip title="Reject suggestion"><IconButton size="small" color="error" onClick={() => onChange({ ...field, review: 'rejected' })}><CloseIcon fontSize="small" /></IconButton></Tooltip>
                  </>
                )}
                {!readOnly && field.review === 'rejected' && (
                  <Tooltip title="Undo rejection"><IconButton size="small" onClick={() => onChange({ ...field, review: 'pending' })}><UndoIcon fontSize="small" /></IconButton></Tooltip>
                )}
              </Stack>
            </InputAdornment>
          ),
        },
      }}
    />
  )
}

export function FieldGroupEditor({ group, onChange, readOnly, multilineKeys = [] }: {
  group: FieldGroup; onChange: (g: FieldGroup) => void; readOnly?: boolean; multilineKeys?: string[]
}) {
  return (
    <Grid container spacing={1.5}>
      {Object.entries(group).filter(([, f]) => f && typeof f === 'object' && 'review' in f).map(([key, field]) => (
        <Grid key={key} size={{ xs: 12, md: multilineKeys.includes(key) ? 12 : 6 }}>
          <ReviewFieldInput label={humanize(key)} field={field} readOnly={readOnly} multiline={multilineKeys.includes(key)}
            onChange={(f) => onChange({ ...group, [key]: f })} />
        </Grid>
      ))}
    </Grid>
  )
}

/** Repeating rows (medicines, lab results, ...); `extra` renders per-row additions such as LOINC mapping. */
export function FieldListEditor<T extends FieldGroup>({ rows, keys, onChange, readOnly, extra, addLabel }: {
  rows: T[]; keys: string[]; onChange: (rows: T[]) => void; readOnly?: boolean
  extra?: (row: T, index: number) => React.ReactNode; addLabel: string
}) {
  return (
    <Stack spacing={1.5}>
      {rows.map((row, i) => (
        <Paper key={i} variant="outlined" sx={{ p: 1.5 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="caption" color="text.secondary">#{i + 1}</Typography>
            {!readOnly && <IconButton size="small" onClick={() => onChange(rows.filter((_, j) => j !== i))}><DeleteIcon fontSize="small" /></IconButton>}
          </Stack>
          <FieldGroupEditor group={row} readOnly={readOnly} onChange={(g) => onChange(rows.map((r, j) => (j === i ? { ...r, ...g } : r)))} />
          {extra?.(row, i)}
        </Paper>
      ))}
      {rows.length === 0 && <Typography variant="body2" color="text.secondary">Nothing extracted.</Typography>}
      {!readOnly && (
        <Box>
          <Button size="small" startIcon={<AddIcon />}
            onClick={() => onChange([...rows, Object.fromEntries(keys.map((k) => [k, blankField()])) as T])}>
            {addLabel}
          </Button>
        </Box>
      )}
    </Stack>
  )
}

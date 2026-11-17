import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Chip, Paper, Stack, Typography } from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { api, ClinicalDocument, DOCUMENT_TYPES, errorMessage, formatDateTime, PatientSummary } from '../api'
import { useAuth } from '../auth'
import { PatientPicker, StatusChip } from '../components/clinical'
import { FilterTabs, FormDialog, PagedTable, PageHeader } from '../components/ui'
import { usePaged } from '../usePaged'

const TABS = [
  { label: 'In review', status: 'draft,rejected' },
  { label: 'Awaiting approval', status: 'pending_approval' },
  { label: 'Approved', status: 'approved' },
  { label: 'All', status: '' },
]

export const EXTRACTION_CHIP = {
  pending: <Chip size="small" label="AI extracting…" color="info" />,
  completed: <Chip size="small" label="Extracted" variant="outlined" />,
  failed: <Chip size="small" label="Manual entry" color="warning" variant="outlined" />,
}

export default function Documents() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [tab, setTab] = useState(0)
  const [uploading, setUploading] = useState(false)
  const paged = usePaged<ClinicalDocument>('/documents', { status: TABS[tab].status })

  return (
    <>
      <PageHeader title="Clinical documents">
        {can('document:write') && <Button variant="contained" startIcon={<UploadFileIcon />} onClick={() => setUploading(true)}>Upload document</Button>}
      </PageHeader>
      <Paper>
        <FilterTabs labels={TABS.map((t) => t.label)} value={tab} onChange={setTab} />
        <PagedTable paged={paged} empty="No documents." onRowClick={(d) => navigate(`/documents/${d.id}`)} columns={[
          ['Uploaded', (d) => <>{formatDateTime(d.created_at)}<br /><Typography variant="caption">{d.created_by_name}</Typography></>, { whiteSpace: 'nowrap' }],
          ['File', (d) => d.file_name],
          ['Type', (d) => DOCUMENT_TYPES[d.document_type]],
          ['Patient', (d) => (d.patient ? `${d.patient.full_name} (${d.patient.patient_code})` : <Chip size="small" label="Not linked" color="warning" variant="outlined" />)],
          ['Extraction', (d) => EXTRACTION_CHIP[d.extraction_status]],
          ['Status', (d) => <StatusChip status={d.status} />],
        ]} />
      </Paper>
      {uploading && <UploadDialog onClose={() => setUploading(false)} />}
    </>
  )
}

function UploadDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [patient, setPatient] = useState<PatientSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const upload = async () => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    if (patient) form.append('patient_id', String(patient.id))
    setBusy(true)
    try {
      const { data } = await api.post<ClinicalDocument>('/documents', form)
      navigate(`/documents/${data.id}`)
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  return (
    <FormDialog title="Upload clinical document" onClose={onClose}
      actions={<Button variant="contained" disabled={!file || busy} onClick={upload}>{busy ? 'Uploading…' : 'Upload and extract'}</Button>}>
      <Stack spacing={2} pt={1}>
        {error && <Alert severity="error">{error}</Alert>}
        <Alert severity="info">
          PDF (text or scanned), JPG, PNG, WEBP, DOCX or TXT, up to 20 MB. The document is sent to the configured AI
          provider for extraction; every value is reviewed by staff before approval.
        </Alert>
        <Button variant="outlined" component="label" startIcon={<UploadFileIcon />}>
          {file ? file.name : 'Choose file'}
          <input hidden type="file" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.docx,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Button>
        <PatientPicker onChange={setPatient} />
        <Typography variant="caption" color="text.secondary">The patient can also be linked during review.</Typography>
      </Stack>
    </FormDialog>
  )
}

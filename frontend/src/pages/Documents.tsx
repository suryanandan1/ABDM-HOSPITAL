import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, LinearProgress, Paper, Stack, Tab,
  Table, TableBody, TableCell, TableHead, TablePagination, TableRow, Tabs, Typography,
} from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { api, ClinicalDocument, DOCUMENT_TYPES, errorMessage, formatDateTime, PatientSummary } from '../api'
import { useAuth } from '../auth'
import { PatientPicker, StatusChip } from '../components/clinical'
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
  const { data, loading, error, pagination } = usePaged<ClinicalDocument>('/documents', { status: TABS[tab].status })

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Clinical documents</Typography>
        {can('document:write') && <Button variant="contained" startIcon={<UploadFileIcon />} onClick={() => setUploading(true)}>Upload document</Button>}
      </Stack>
      <Paper>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
          {TABS.map((t) => <Tab key={t.label} label={t.label} />)}
        </Tabs>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Uploaded</TableCell>
                <TableCell>File</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Patient</TableCell>
                <TableCell>Extraction</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((d) => (
                <TableRow key={d.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/documents/${d.id}`)}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(d.created_at)}<br /><Typography variant="caption">{d.created_by_name}</Typography></TableCell>
                  <TableCell>{d.file_name}</TableCell>
                  <TableCell>{DOCUMENT_TYPES[d.document_type]}</TableCell>
                  <TableCell>{d.patient ? `${d.patient.full_name} (${d.patient.patient_code})` : <Chip size="small" label="Not linked" color="warning" variant="outlined" />}</TableCell>
                  <TableCell>{EXTRACTION_CHIP[d.extraction_status]}</TableCell>
                  <TableCell><StatusChip status={d.status} /></TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && <TableRow><TableCell colSpan={6}>No documents.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
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
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Upload clinical document</DialogTitle>
      <DialogContent>
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
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!file || busy} onClick={upload}>{busy ? 'Uploading…' : 'Upload and extract'}</Button>
      </DialogActions>
    </Dialog>
  )
}

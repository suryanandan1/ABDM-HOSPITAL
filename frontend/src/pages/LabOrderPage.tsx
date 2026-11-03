import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Grid2 as Grid, MenuItem, Paper, Skeleton, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material'
import PrintIcon from '@mui/icons-material/Print'
import { api, errorMessage, formatDateTime, formatRange, LabOrder, LabResult } from '../api'
import { useAuth } from '../auth'
import { FhirButton, PatientBanner, ReasonDialog, StatusChip } from '../components/clinical'

const FLAG: Record<string, { label: string; color: 'success' | 'error' | 'warning' }> = {
  N: { label: 'Normal', color: 'success' }, L: { label: 'Low', color: 'warning' },
  H: { label: 'High', color: 'error' }, A: { label: 'Abnormal', color: 'error' },
}

export default function LabOrderPage() {
  const { id } = useParams()
  const { can, user } = useAuth()
  const navigate = useNavigate()
  const [order, setOrder] = useState<LabOrder | null>(null)
  const [values, setValues] = useState<Record<string, { value: string; interpretation: string }>>({})
  const [conclusion, setConclusion] = useState('')
  const [sample, setSample] = useState({ sample_barcode: '', notes: '' })
  const [dialog, setDialog] = useState<'cancel' | 'reject-sample' | 'reject-report' | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = (o: LabOrder) => {
    setOrder(o)
    const byCode = Object.fromEntries((o.report?.results ?? []).map((r) => [r.code, r]))
    setValues(Object.fromEntries(o.test.parameters.map((p) => [p.code, {
      value: byCode[p.code]?.value ?? '', interpretation: byCode[p.code]?.interpretation ?? '',
    }])))
    setConclusion(o.report?.conclusion ?? '')
  }
  useEffect(() => {
    api.get<LabOrder>(`/lab/orders/${id}`).then((r) => load(r.data)).catch((e) => setError(errorMessage(e)))
  }, [id])

  if (!order) return error ? <Alert severity="error">{error}</Alert> : <Skeleton variant="rounded" height={300} />

  const report = order.report
  const canEnter = can('lab:result') && ['sample_collected', 'in_progress'].includes(order.status) &&
    (!report || ['draft', 'rejected'].includes(report.status))
  const canApprove = can('lab:approve') && Boolean(user?.doctor_id) && report?.status === 'pending_approval'

  const act = async (request: () => Promise<{ data: LabOrder }>, success: string) => {
    try {
      load((await request()).data)
      setMessage(success)
      setError('')
    } catch (e) {
      setError(errorMessage(e))
      throw e
    }
  }
  const saveResults = () => api.put<LabOrder>(`/lab/orders/${id}/report`, {
    conclusion: conclusion || null,
    results: order.test.parameters.map((p) => ({
      code: p.code, value: values[p.code]?.value || null, interpretation: values[p.code]?.interpretation || null,
    })),
  })
  const quiet = (p: Promise<unknown>) => p.catch(() => undefined)

  return (
    <>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} mb={2} spacing={1} className="no-print">
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">Lab order #{order.id}</Typography>
          <StatusChip status={order.status} />
          {report?.status === 'approved' && <FhirButton path={`/fhir/lab-orders/${id}`} source={['lab_order', Number(id)]} />}
        </Stack>
        <Stack direction="row" spacing={1}>
          {can('clinical:read') && <Button onClick={() => navigate(`/consultations/${order.encounter_id}`)}>Open consultation</Button>}
          {report?.status === 'approved' && <Button startIcon={<PrintIcon />} onClick={() => window.print()}>Print report</Button>}
          {can('lab:order') && ['ordered', 'sample_collected'].includes(order.status) && (
            <Button color="error" onClick={() => setDialog('cancel')}>Cancel order</Button>
          )}
        </Stack>
      </Stack>
      <Box className="no-print">
        {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage('')}>{message}</Alert>}
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
        {order.status === 'cancelled' && <Alert severity="warning" sx={{ mb: 2 }}>Cancelled: {order.status_reason}</Alert>}
        {report?.status === 'rejected' && <Alert severity="warning" sx={{ mb: 2 }}>Report returned for correction: {report.status_reason}</Alert>}
      </Box>

      <Paper sx={{ p: 3 }}>
        {report?.status === 'approved' && <Typography variant="h6" mb={1}>{user?.hospital_name} — Laboratory report</Typography>}
        <PatientBanner patient={order.patient} />
        <Grid container spacing={2} mb={3}>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">Test</Typography>
            <Typography fontWeight={600}>{order.test.name}</Typography>
            <Typography variant="body2">Priority: {order.priority.toUpperCase()}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">Ordered by</Typography>
            <Typography>{order.doctor.full_name}</Typography>
            <Typography variant="body2">{formatDateTime(order.created_at)}</Typography>
            {order.clinical_notes && <Typography variant="body2">Notes: {order.clinical_notes}</Typography>}
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">Sample ({order.test.sample_type})</Typography>
            {order.sample_status === 'collected' ? (
              <>
                <Typography>{order.sample_barcode}</Typography>
                <Typography variant="body2">Collected {formatDateTime(order.sample_collected_at)} by {order.sample_collected_by_name}</Typography>
              </>
            ) : (
              <Typography>{order.sample_status === 'rejected' ? `Rejected: ${order.sample_notes}` : 'Not collected'}</Typography>
            )}
          </Grid>
        </Grid>

        {order.status === 'ordered' && can('lab:sample') && (
          <Paper sx={{ p: 2, mb: 3, bgcolor: '#f7fbfd' }} className="no-print">
            <Typography variant="subtitle1" fontWeight={600} mb={1}>Collect sample</Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField label="Sample barcode (blank = auto)" value={sample.sample_barcode} onChange={(e) => setSample({ ...sample, sample_barcode: e.target.value })} />
              <TextField label="Notes" value={sample.notes} onChange={(e) => setSample({ ...sample, notes: e.target.value })} />
              <Button variant="contained" sx={{ flexShrink: 0 }}
                onClick={() => quiet(act(() => api.post(`/lab/orders/${id}/sample`, sample), 'Sample collected.'))}>
                Mark collected
              </Button>
            </Stack>
          </Paper>
        )}

        {canEnter ? (
          <Box className="no-print">
            <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
              <Typography variant="subtitle1" fontWeight={600}>Enter results</Typography>
              {order.status === 'sample_collected' && can('lab:sample') && (
                <Button size="small" color="error" onClick={() => setDialog('reject-sample')}>Reject sample</Button>
              )}
            </Stack>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Parameter</TableCell>
                  <TableCell>Result</TableCell>
                  <TableCell>Unit</TableCell>
                  <TableCell>Reference range</TableCell>
                  <TableCell>Flag</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {order.test.parameters.map((p) => {
                  const numericRange = p.ref_low != null || p.ref_high != null
                  const saved = report?.results.find((r) => r.code === p.code)
                  const set = (change: Partial<{ value: string; interpretation: string }>) =>
                    setValues({ ...values, [p.code]: { ...values[p.code], ...change } })
                  return (
                    <TableRow key={p.code}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell sx={{ width: 180 }}>
                        <TextField value={values[p.code]?.value ?? ''} type={numericRange ? 'number' : 'text'}
                          slotProps={{ htmlInput: { step: 'any' } }} onChange={(e) => set({ value: e.target.value })} />
                      </TableCell>
                      <TableCell>{p.unit}</TableCell>
                      <TableCell>{formatRange(p)}</TableCell>
                      <TableCell sx={{ width: 160 }}>
                        {numericRange
                          ? saved?.interpretation && <ResultFlag result={saved} />
                          : (
                            <TextField select value={values[p.code]?.interpretation ?? ''} onChange={(e) => set({ interpretation: e.target.value })}>
                              <MenuItem value="">—</MenuItem>
                              <MenuItem value="N">Normal</MenuItem>
                              <MenuItem value="A">Abnormal</MenuItem>
                            </TextField>
                          )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <TextField label="Conclusion / remarks" multiline minRows={2} value={conclusion} sx={{ mt: 2 }} onChange={(e) => setConclusion(e.target.value)} />
            <Typography variant="body2" color="text.secondary" mt={1}>Numeric results are flagged against the reference range when saved.</Typography>
            <Stack direction="row" spacing={1} justifyContent="flex-end" mt={2}>
              <Button variant="outlined" onClick={() => quiet(act(saveResults, 'Results saved as draft.'))}>Save draft</Button>
              <Button variant="contained" onClick={() => quiet(act(saveResults, '').then(() =>
                act(() => api.post(`/lab/orders/${id}/report/submit`), 'Report submitted for approval.')))}>
                Submit for approval
              </Button>
            </Stack>
          </Box>
        ) : report && (
          <>
            <Stack direction="row" spacing={1} alignItems="center" mb={1}>
              <Typography variant="subtitle1" fontWeight={600}>Results</Typography>
              <Box className="no-print"><StatusChip status={report.status} /></Box>
            </Stack>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Parameter</TableCell>
                  <TableCell>Result</TableCell>
                  <TableCell>Unit</TableCell>
                  <TableCell>Reference range</TableCell>
                  <TableCell>Flag</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {report.results.map((r) => (
                  <TableRow key={r.code}>
                    <TableCell>{r.name}</TableCell>
                    <TableCell sx={{ fontWeight: r.interpretation && r.interpretation !== 'N' ? 700 : 400 }}>{r.value}</TableCell>
                    <TableCell>{r.unit}</TableCell>
                    <TableCell>{formatRange(r)}</TableCell>
                    <TableCell>{r.interpretation && <ResultFlag result={r} />}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {report.conclusion && <Typography mt={2}><b>Conclusion:</b> {report.conclusion}</Typography>}
            <Typography variant="body2" color="text.secondary" mt={2}>
              Results entered by {report.created_by_name} on {formatDateTime(report.updated_at)}.
              {report.approved_by_name && ` Approved by ${report.approved_by_name} on ${formatDateTime(report.approved_at)}.`}
            </Typography>
            {canApprove && (
              <Stack direction="row" spacing={1} justifyContent="flex-end" mt={2} className="no-print">
                <Button color="error" onClick={() => setDialog('reject-report')}>Return for correction</Button>
                <Button variant="contained" onClick={() => quiet(act(() => api.post(`/lab/orders/${id}/report/approve`), 'Report approved and released.'))}>
                  Approve report
                </Button>
              </Stack>
            )}
          </>
        )}
      </Paper>

      {dialog && (
        <ReasonDialog
          title={{ cancel: 'Cancel order', 'reject-sample': 'Reject sample', 'reject-report': 'Return report for correction' }[dialog]}
          onClose={() => setDialog(null)}
          onSubmit={(reason) => act(() => api.post(`/lab/orders/${id}/${{
            cancel: 'cancel', 'reject-sample': 'sample/reject', 'reject-report': 'report/reject' }[dialog]}`, { reason }), 'Done.')} />
      )}
    </>
  )
}

function ResultFlag({ result }: { result: Pick<LabResult, 'interpretation'> }) {
  const flag = FLAG[result.interpretation ?? '']
  return flag ? <Chip size="small" label={flag.label} color={flag.color} variant={result.interpretation === 'N' ? 'outlined' : 'filled'} /> : null
}

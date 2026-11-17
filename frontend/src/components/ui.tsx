import { Key, ReactNode } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid2 as Grid, LinearProgress, Paper,
  Stack, SxProps, Tab, Table, TableBody, TableCell, TableHead, TablePagination, TableRow, Tabs, Typography,
} from '@mui/material'
import { PatientSummary } from '../api'
import { usePaged } from '../usePaged'

/** Modal form with a Cancel button followed by `actions`; `leading` actions sit at the far left. */
export function FormDialog({ title, onClose, actions, leading, maxWidth = 'sm', children }: {
  title: ReactNode; onClose: () => void; actions: ReactNode; leading?: ReactNode; maxWidth?: 'sm' | 'md'
  children: ReactNode
}) {
  return (
    <Dialog open onClose={onClose} maxWidth={maxWidth} fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>{children}</DialogContent>
      <DialogActions>{leading}<Button onClick={onClose}>Cancel</Button>{actions}</DialogActions>
    </Dialog>
  )
}

/** [header, cell renderer, optional cell style] */
export type Column<T> = [label: string, cell: (row: T) => ReactNode, sx?: SxProps]

/** Page title with optional actions on the right. */
export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
      <Typography variant="h5">{title}</Typography>
      {children}
    </Stack>
  )
}

/** Tabs strip used as a status filter above a list. */
export function FilterTabs({ labels, value, onChange, scrollable = true }: {
  labels: string[]; value: number; onChange: (tab: number) => void; scrollable?: boolean
}) {
  return (
    <Tabs value={value} onChange={(_, v) => onChange(v)} variant={scrollable ? 'scrollable' : 'standard'}
      sx={{ px: 1, borderBottom: '1px solid #e3e7ed' }}>
      {labels.map((label) => <Tab key={label} label={label} />)}
    </Tabs>
  )
}

/** Grid of small counter tiles. */
export function StatTiles({ tiles }: { tiles: readonly (readonly [string, number | undefined])[] }) {
  return (
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
  )
}

export const PatientCell =({ patient }: { patient: PatientSummary }) => (
  <>{patient.full_name}<br /><Typography variant="caption">{patient.patient_code}</Typography></>
)

export const ActiveChip = ({ active }: { active: boolean }) => (
  <Chip size="small" label={active ? 'Active' : 'Inactive'} color={active ? 'success' : 'default'} />
)

/** Scrollable small table; rows are clickable when `onRowClick` is given, `empty` is shown when there are no rows. */
export function DataTable<T>({ rows, columns, empty, onRowClick, rowKey = (row) => (row as { id: Key }).id }: {
  rows: T[]; columns: Column<T>[]; empty?: string; onRowClick?: (row: T) => void; rowKey?: (row: T) => Key
}) {
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Table size="small">
        <TableHead><TableRow>{columns.map(([label]) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={rowKey(row)} hover={Boolean(onRowClick)} sx={onRowClick && { cursor: 'pointer' }}
              onClick={onRowClick && (() => onRowClick(row))}>
              {columns.map(([label, cell, sx]) => <TableCell key={label} sx={sx}>{cell(row)}</TableCell>)}
            </TableRow>
          ))}
          {empty && rows.length === 0 && <TableRow><TableCell colSpan={columns.length}>{empty}</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Box>
  )
}

/** A `usePaged` list: progress bar, error, table and pagination. */
export function PagedTable<T extends { id: number }>({ paged, rowsPerPage = [10, 20, 50], empty, ...table }: {
  paged: ReturnType<typeof usePaged<T>>; columns: Column<T>[]; empty?: string; onRowClick?: (row: T) => void
  rowsPerPage?: number[]
}) {
  return (
    <>
      {paged.loading && <LinearProgress />}
      {paged.error && <Alert severity="error">{paged.error}</Alert>}
      <DataTable rows={paged.data.items} empty={paged.loading ? undefined : empty} {...table} />
      <TablePagination component="div" rowsPerPageOptions={rowsPerPage} {...paged.pagination} />
    </>
  )
}

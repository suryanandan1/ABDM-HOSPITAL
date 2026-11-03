import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, InputAdornment, LinearProgress, MenuItem, Paper, Stack, Table, TableBody, TableCell,
  TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import { ageOf, formatDate, GENDERS, PatientSummary } from '../api'
import { useAuth } from '../auth'
import AbhaStatus from '../components/AbhaStatus'
import { usePaged } from '../usePaged'

export default function Patients() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [abha, setAbha] = useState('')
  const { data, loading, error, pagination } = usePaged<PatientSummary>('/patients', { q: query, abha_verified: abha })

  return (
    <>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5">Patients</Typography>
        {can('patient:create') && (
          <Button variant="contained" startIcon={<PersonAddIcon />} onClick={() => navigate('/patients/new')}>
            Register patient
          </Button>
        )}
      </Stack>
      <Paper>
        <Stack component="form" direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ p: 2 }}
          onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()) }}>
          <TextField
            placeholder="Search by patient ID, name, mobile, or verified ABHA number / address"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon /></InputAdornment> } }}
          />
          <TextField select label="ABHA" value={abha} onChange={(e) => setAbha(e.target.value)} sx={{ width: { sm: 200 } }}>
            <MenuItem value="">All</MenuItem>
            <MenuItem value="true">Verified</MenuItem>
            <MenuItem value="false">Not verified</MenuItem>
          </TextField>
          <Button type="submit" variant="outlined">Search</Button>
        </Stack>
        {loading && <LinearProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Patient ID</TableCell>
                <TableCell>Name</TableCell>
                <TableCell>Age / Gender</TableCell>
                <TableCell>Mobile</TableCell>
                <TableCell>ABHA</TableCell>
                <TableCell>Registered</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((p) => (
                <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/patients/${p.id}`)}>
                  <TableCell>{p.patient_code}</TableCell>
                  <TableCell>{p.full_name}</TableCell>
                  <TableCell>{ageOf(p)} / {GENDERS[p.gender]}</TableCell>
                  <TableCell>{p.mobile}</TableCell>
                  <TableCell><AbhaStatus patient={p} /></TableCell>
                  <TableCell>{formatDate(p.created_at)}</TableCell>
                </TableRow>
              ))}
              {!loading && data.items.length === 0 && (
                <TableRow><TableCell colSpan={6}>No patients found.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" rowsPerPageOptions={[10, 20, 50]} {...pagination} />
      </Paper>
    </>
  )
}

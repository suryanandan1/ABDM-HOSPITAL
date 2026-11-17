import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, InputAdornment, MenuItem, Paper, Stack, TextField } from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import { ageOf, formatDate, GENDERS, PatientSummary } from '../api'
import { useAuth } from '../auth'
import AbhaStatus from '../components/AbhaStatus'
import { PagedTable, PageHeader } from '../components/ui'
import { usePaged } from '../usePaged'

export default function Patients() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [abha, setAbha] = useState('')
  const paged = usePaged<PatientSummary>('/patients', { q: query, abha_verified: abha })

  return (
    <>
      <PageHeader title="Patients">
        {can('patient:create') && (
          <Button variant="contained" startIcon={<PersonAddIcon />} onClick={() => navigate('/patients/new')}>
            Register patient
          </Button>
        )}
      </PageHeader>
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
        <PagedTable paged={paged} empty="No patients found." onRowClick={(p) => navigate(`/patients/${p.id}`)} columns={[
          ['Patient ID', (p) => p.patient_code],
          ['Name', (p) => p.full_name],
          ['Age / Gender', (p) => <>{ageOf(p)} / {GENDERS[p.gender]}</>],
          ['Mobile', (p) => p.mobile],
          ['ABHA', (p) => <AbhaStatus patient={p} />],
          ['Registered', (p) => formatDate(p.created_at)],
        ]} />
      </Paper>
    </>
  )
}

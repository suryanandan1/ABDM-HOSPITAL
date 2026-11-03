import { FormEvent, useState } from 'react'
import {
  Button, Divider, FormControlLabel, Grid2 as Grid, MenuItem, Radio, RadioGroup, Stack, TextField, Typography,
} from '@mui/material'
import { formatAbha, GENDERS, Patient } from '../api'

const TEXT_FIELDS = [
  'full_name', 'mobile', 'date_of_birth', 'age', 'address_line', 'city', 'state', 'pincode', 'abha_number',
  'abha_address', 'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relation',
] as const

type FormValues = Record<(typeof TEXT_FIELDS)[number] | 'gender', string>

export type PatientPayload = Omit<Record<keyof FormValues, string | null>, 'age'> & { age: number | null }

function initialValues(patient?: Patient): FormValues {
  const values = Object.fromEntries(TEXT_FIELDS.map((f) => [f, ''])) as FormValues
  values.gender = ''
  if (!patient) return values
  for (const f of TEXT_FIELDS) {
    if (f in patient) values[f] = String(patient[f as keyof Patient] ?? '')
  }
  values.gender = patient.gender
  values.abha_number = formatAbha(patient.abha_number)
  if (!patient.date_of_birth) values.age = String(new Date().getFullYear() - patient.year_of_birth)
  return values
}

interface Props {
  patient?: Patient
  submitLabel: string
  busy?: boolean
  onSubmit: (payload: PatientPayload) => void
  onCancel?: () => void
}

/** Patient demographics form shared by registration and editing. */
export default function PatientForm({ patient, submitLabel, busy, onSubmit, onCancel }: Props) {
  const [values, setValues] = useState<FormValues>(() => initialValues(patient))
  const [dobMode, setDobMode] = useState<'dob' | 'age'>(patient && !patient.date_of_birth ? 'age' : 'dob')

  const set = (name: keyof FormValues) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues({ ...values, [name]: e.target.value })

  const field = (name: keyof FormValues, label: string, props: Record<string, unknown> = {}) => (
    <TextField label={label} name={name} value={values[name]} onChange={set(name)} {...props} />
  )

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const payload = Object.fromEntries(
      Object.entries(values).map(([k, v]) => [k, v.trim() === '' ? null : v.trim()]),
    ) as Record<string, string | null>
    if (dobMode === 'dob') payload.age = null
    else payload.date_of_birth = null
    onSubmit({ ...payload, age: payload.age === null ? null : Number(payload.age) } as PatientPayload)
  }

  const today = new Date().toISOString().slice(0, 10)

  return (
    <form onSubmit={submit}>
      <Stack spacing={3}>
        <section>
          <Typography variant="subtitle1" fontWeight={600} mb={1.5}>Basic details</Typography>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, md: 6 }}>{field('full_name', 'Full name', { required: true })}</Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              {field('gender', 'Gender', {
                select: true, required: true,
                children: Object.entries(GENDERS).map(([code, label]) => <MenuItem key={code} value={code}>{label}</MenuItem>),
              })}
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              {field('mobile', 'Mobile number', { required: true, placeholder: '10-digit mobile' })}
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <RadioGroup row value={dobMode} onChange={(e) => setDobMode(e.target.value as 'dob' | 'age')}>
                <FormControlLabel value="dob" control={<Radio size="small" />} label="Date of birth" />
                <FormControlLabel value="age" control={<Radio size="small" />} label="Age (DOB unknown)" />
              </RadioGroup>
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              {dobMode === 'dob'
                ? field('date_of_birth', 'Date of birth', { type: 'date', required: true, slotProps: { inputLabel: { shrink: true }, htmlInput: { max: today } } })
                : field('age', 'Age in years', { type: 'number', required: true, slotProps: { htmlInput: { min: 0, max: 130 } } })}
            </Grid>
          </Grid>
        </section>

        <Divider />
        <section>
          <Typography variant="subtitle1" fontWeight={600} mb={1.5}>Address</Typography>
          <Grid container spacing={2}>
            <Grid size={12}>{field('address_line', 'Address')}</Grid>
            <Grid size={{ xs: 12, sm: 4 }}>{field('city', 'City / Town')}</Grid>
            <Grid size={{ xs: 12, sm: 4 }}>{field('state', 'State')}</Grid>
            <Grid size={{ xs: 12, sm: 4 }}>{field('pincode', 'Pincode')}</Grid>
          </Grid>
        </section>

        <Divider />
        <section>
          <Typography variant="subtitle1" fontWeight={600}>ABHA (optional)</Typography>
          <Typography variant="body2" color="text.secondary" mb={1.5}>
            Entered ABHA details stay unverified until authorised health information staff verify them.
          </Typography>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 6 }}>{field('abha_number', 'ABHA number', { placeholder: '91-1234-5678-9012' })}</Grid>
            <Grid size={{ xs: 12, sm: 6 }}>{field('abha_address', 'ABHA address', { placeholder: 'name@abdm' })}</Grid>
          </Grid>
        </section>

        <Divider />
        <section>
          <Typography variant="subtitle1" fontWeight={600} mb={1.5}>Emergency contact</Typography>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 5 }}>{field('emergency_contact_name', 'Name')}</Grid>
            <Grid size={{ xs: 12, sm: 4 }}>{field('emergency_contact_phone', 'Mobile number')}</Grid>
            <Grid size={{ xs: 12, sm: 3 }}>{field('emergency_contact_relation', 'Relation')}</Grid>
          </Grid>
        </section>

        <Stack direction="row" spacing={1} justifyContent="flex-end">
          {onCancel && <Button onClick={onCancel}>Cancel</Button>}
          <Button type="submit" variant="contained" disabled={busy}>{submitLabel}</Button>
        </Stack>
      </Stack>
    </form>
  )
}

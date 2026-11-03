import { Chip } from '@mui/material'
import VerifiedIcon from '@mui/icons-material/Verified'
import { PatientSummary } from '../api'

/** ABHA state of a patient: none, entered but unverified, or verified. */
export default function AbhaStatus({ patient }: { patient: PatientSummary }) {
  if (!patient.abha_number && !patient.abha_address) return <Chip size="small" label="No ABHA" variant="outlined" />
  if (!patient.abha_verified) return <Chip size="small" label="ABHA unverified" color="warning" variant="outlined" />
  return <Chip size="small" label="ABHA verified" color="success" icon={<VerifiedIcon />} />
}

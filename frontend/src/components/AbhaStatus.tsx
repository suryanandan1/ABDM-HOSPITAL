import { Chip } from '@mui/material'
import VerifiedIcon from '@mui/icons-material/Verified'
import { PatientSummary } from '../api'

/** ABHA state of a patient: none, entered but unverified, or verified; shows the ABHA address (e.g. surya@abdm). */
export default function AbhaStatus({ patient }: { patient: PatientSummary }) {
  if (!patient.abha_number && !patient.abha_address) return <Chip size="small" label="No ABHA" variant="outlined" />
  const address = patient.abha_address
  if (!patient.abha_verified) {
    return <Chip size="small" label={address ? `${address} · unverified` : 'ABHA unverified'} color="warning" variant="outlined" />
  }
  return <Chip size="small" label={address ?? 'ABHA verified'} title="ABHA verified with ABDM" color="success" icon={<VerifiedIcon />} />
}

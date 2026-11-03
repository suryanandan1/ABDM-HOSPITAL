import { lazy, ReactNode, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Alert, LinearProgress } from '@mui/material'
import { useAuth } from './auth'
import Layout from './components/Layout'
import Login from './pages/Login'
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Patients = lazy(() => import('./pages/Patients'))
const PatientRegister = lazy(() => import('./pages/PatientRegister'))
const PatientDetail = lazy(() => import('./pages/PatientDetail'))
const Doctors = lazy(() => import('./pages/Doctors'))
const Staff = lazy(() => import('./pages/Staff'))
const HospitalProfile = lazy(() => import('./pages/HospitalProfile'))
const AuditLogs = lazy(() => import('./pages/AuditLogs'))
const Consultations = lazy(() => import('./pages/Consultations'))
const ConsultationPage = lazy(() => import('./pages/ConsultationPage'))
const Prescriptions = lazy(() => import('./pages/Prescriptions'))
const PrescriptionPage = lazy(() => import('./pages/PrescriptionPage'))
const LabTests = lazy(() => import('./pages/LabTests'))
const LabOrders = lazy(() => import('./pages/LabOrders'))
const LabOrderPage = lazy(() => import('./pages/LabOrderPage'))
const PatientHistory = lazy(() => import('./pages/PatientHistory'))
const Documents = lazy(() => import('./pages/Documents'))
const DocumentReview = lazy(() => import('./pages/DocumentReview'))
const FhirRecords = lazy(() => import('./pages/FhirRecords'))
const FhirRecordPage = lazy(() => import('./pages/FhirRecords').then((m) => ({ default: m.FhirRecordPage })))
const Hip = lazy(() => import('./pages/Hip'))
const Hiu = lazy(() => import('./pages/Hiu'))
const Assistant = lazy(() => import('./pages/Assistant'))

function Guard({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  if (!can(permission)) return <Alert severity="warning">You do not have access to this page.</Alert>
  return <Suspense fallback={<LinearProgress />}>{children}</Suspense>
}

export default function App() {
  const { user } = useAuth()
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Guard permission="dashboard:view"><Dashboard /></Guard>} />
        <Route path="patients" element={<Guard permission="patient:read"><Patients /></Guard>} />
        <Route path="patients/new" element={<Guard permission="patient:create"><PatientRegister /></Guard>} />
        <Route path="patients/:id" element={<Guard permission="patient:read"><PatientDetail /></Guard>} />
        <Route path="doctors" element={<Guard permission="doctor:read"><Doctors /></Guard>} />
        <Route path="staff" element={<Guard permission="staff:read"><Staff /></Guard>} />
        <Route path="hospital" element={<Guard permission="hospital:read"><HospitalProfile /></Guard>} />
        <Route path="audit-logs" element={<Guard permission="audit:read"><AuditLogs /></Guard>} />
        <Route path="patients/:id/history" element={<Guard permission="clinical:read"><PatientHistory /></Guard>} />
        <Route path="consultations" element={<Guard permission="clinical:read"><Consultations /></Guard>} />
        <Route path="consultations/:id" element={<Guard permission="clinical:read"><ConsultationPage /></Guard>} />
        <Route path="prescriptions" element={<Guard permission="clinical:read"><Prescriptions /></Guard>} />
        <Route path="prescriptions/:id" element={<Guard permission="clinical:read"><PrescriptionPage /></Guard>} />
        <Route path="lab/tests" element={<Guard permission="lab:read"><LabTests /></Guard>} />
        <Route path="lab/orders" element={<Guard permission="lab:read"><LabOrders /></Guard>} />
        <Route path="lab/orders/:id" element={<Guard permission="lab:read"><LabOrderPage /></Guard>} />
        <Route path="documents" element={<Guard permission="document:read"><Documents /></Guard>} />
        <Route path="documents/:id" element={<Guard permission="document:read"><DocumentReview /></Guard>} />
        <Route path="fhir/records" element={<Guard permission="fhir:read"><FhirRecords /></Guard>} />
        <Route path="fhir/records/:id" element={<Guard permission="fhir:read"><FhirRecordPage /></Guard>} />
        <Route path="hip" element={<Guard permission="hip:read"><Hip /></Guard>} />
        <Route path="hiu" element={<Guard permission="hiu:read"><Hiu /></Guard>} />
        <Route path="assistant/:patientId" element={<Guard permission="clinical:read"><Assistant /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

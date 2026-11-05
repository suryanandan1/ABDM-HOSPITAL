import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Alert, AppBar, Avatar, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Drawer,
  IconButton, List, ListItemButton, ListItemIcon, ListItemText, Menu, MenuItem, Stack, TextField, Toolbar,
  Typography, useMediaQuery, useTheme,
} from '@mui/material'
import DashboardIcon from '@mui/icons-material/Dashboard'
import PeopleIcon from '@mui/icons-material/People'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import MedicalServicesIcon from '@mui/icons-material/MedicalServices'
import BadgeIcon from '@mui/icons-material/Badge'
import LocalHospitalIcon from '@mui/icons-material/LocalHospital'
import HistoryIcon from '@mui/icons-material/History'
import MenuIcon from '@mui/icons-material/Menu'
import AssignmentIcon from '@mui/icons-material/Assignment'
import MedicationIcon from '@mui/icons-material/Medication'
import ScienceIcon from '@mui/icons-material/Science'
import BiotechIcon from '@mui/icons-material/Biotech'
import DocumentScannerIcon from '@mui/icons-material/DocumentScanner'
import DataObjectIcon from '@mui/icons-material/DataObject'
import HubIcon from '@mui/icons-material/Hub'
import TravelExploreIcon from '@mui/icons-material/TravelExplore'
import { api, errorMessage } from '../api'
import { useAuth } from '../auth'
import MfaSetup from './MfaSetup'

const DRAWER_WIDTH = 240

const NAV = [
  { to: '/', label: 'Dashboard', icon: <DashboardIcon />, permission: 'dashboard:view' },
  { to: '/patients', label: 'Patients', icon: <PeopleIcon />, permission: 'patient:read' },
  { to: '/patients/new', label: 'Register Patient', icon: <PersonAddIcon />, permission: 'patient:create' },
  { to: '/consultations', label: 'Consultations', icon: <AssignmentIcon />, permission: 'clinical:read' },
  { to: '/prescriptions', label: 'Prescriptions', icon: <MedicationIcon />, permission: 'clinical:read' },
  { to: '/lab/orders', label: 'Lab Orders', icon: <ScienceIcon />, permission: 'lab:read' },
  { to: '/lab/tests', label: 'Lab Catalogue', icon: <BiotechIcon />, permission: 'lab:read' },
  { to: '/documents', label: 'AI Documents', icon: <DocumentScannerIcon />, permission: 'document:read' },
  { to: '/fhir/records', label: 'FHIR Records', icon: <DataObjectIcon />, permission: 'fhir:read' },
  { to: '/hip', label: 'ABDM HIP', icon: <HubIcon />, permission: 'hip:read' },
  { to: '/hiu', label: 'External Records (HIU)', icon: <TravelExploreIcon />, permission: 'hiu:read' },
  { to: '/doctors', label: 'Doctors', icon: <MedicalServicesIcon />, permission: 'doctor:read' },
  { to: '/staff', label: 'Staff', icon: <BadgeIcon />, permission: 'staff:read' },
  { to: '/hospital', label: 'Hospital Profile', icon: <LocalHospitalIcon />, permission: 'hospital:read' },
  { to: '/audit-logs', label: 'Audit Logs', icon: <HistoryIcon />, permission: 'audit:read' },
]

export default function Layout() {
  const { user, can, logout, logoutEverywhere } = useAuth()
  const navigate = useNavigate()
  const theme = useTheme()
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'))
  const [mobileOpen, setMobileOpen] = useState(false)
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [mfaOpen, setMfaOpen] = useState(false)
  const mustSetUpMfa = Boolean(user?.mfa_setup_required)

  const drawer = (
    <>
      <Toolbar sx={{ gap: 1 }}>
        <LocalHospitalIcon color="primary" />
        <Typography variant="subtitle1" fontWeight={700} noWrap>
          ABDM Hospital
        </Typography>
      </Toolbar>
      <Divider />
      <List sx={{ px: 1 }}>
        {NAV.filter((item) => !mustSetUpMfa && can(item.permission)).map((item) => (
          <ListItemButton
            key={item.to}
            component={NavLink}
            to={item.to}
            end
            onClick={() => setMobileOpen(false)}
            sx={{ borderRadius: 1, mb: 0.5, '&.active': { bgcolor: 'primary.main', color: 'white', '& svg': { color: 'white' } } }}
          >
            <ListItemIcon sx={{ minWidth: 36 }}>{item.icon}</ListItemIcon>
            <ListItemText primary={item.label} />
          </ListItemButton>
        ))}
      </List>
    </>
  )

  return (
    <Box sx={{ display: 'flex' }}>
      <AppBar position="fixed" color="inherit" className="no-print" sx={{ ml: { md: `${DRAWER_WIDTH}px` }, width: { md: `calc(100% - ${DRAWER_WIDTH}px)` }, borderBottom: '1px solid #e3e7ed' }}>
        <Toolbar>
          {!isDesktop && (
            <IconButton edge="start" onClick={() => setMobileOpen(true)} sx={{ mr: 1 }}>
              <MenuIcon />
            </IconButton>
          )}
          <Typography variant="h6" noWrap sx={{ flexGrow: 1 }}>
            {user?.hospital_name}
          </Typography>
          <Button color="inherit" onClick={(e) => setMenuAnchor(e.currentTarget)} startIcon={<Avatar sx={{ width: 28, height: 28, fontSize: 14, bgcolor: 'primary.main' }}>{user?.full_name[0]}</Avatar>}>
            <Box sx={{ textAlign: 'left', display: { xs: 'none', sm: 'block' } }}>
              <Typography variant="body2" lineHeight={1.2}>{user?.full_name}</Typography>
              <Typography variant="caption" color="text.secondary">{user?.role_name}</Typography>
            </Box>
          </Button>
          <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)}>
            <MenuItem onClick={() => { setMenuAnchor(null); setPasswordOpen(true) }}>Change password</MenuItem>
            <MenuItem onClick={() => { setMenuAnchor(null); setMfaOpen(true) }}>Two-step sign-in</MenuItem>
            <Divider />
            <MenuItem onClick={() => logout().then(() => navigate('/login'))}>Sign out</MenuItem>
            <MenuItem onClick={() => logoutEverywhere().then(() => navigate('/login'))}>Sign out everywhere</MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>

      <Box component="nav" className="no-print" sx={{ width: { md: DRAWER_WIDTH }, flexShrink: 0 }}>
        <Drawer
          variant={isDesktop ? 'permanent' : 'temporary'}
          open={isDesktop || mobileOpen}
          onClose={() => setMobileOpen(false)}
          sx={{ '& .MuiDrawer-paper': { width: DRAWER_WIDTH, borderRight: '1px solid #e3e7ed' } }}
        >
          {drawer}
        </Drawer>
      </Box>

      <Box component="main" sx={{ flexGrow: 1, p: { xs: 2, md: 3 }, minWidth: 0 }}>
        <Toolbar className="no-print" />
        {mustSetUpMfa ? (
          <Box sx={{ maxWidth: 560 }}>
            <Typography variant="h5" gutterBottom>Set up two-step sign-in</Typography>
            <Alert severity="warning" sx={{ mb: 2 }}>Your role requires two-step sign-in. Set it up to continue.</Alert>
            <MfaSetup />
          </Box>
        ) : <Outlet />}
      </Box>

      {passwordOpen && <ChangePasswordDialog onClose={() => setPasswordOpen(false)} />}
      {mfaOpen && (
        <Dialog open onClose={() => setMfaOpen(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Two-step sign-in</DialogTitle>
          <DialogContent><Box sx={{ pt: 1 }}><MfaSetup /></Box></DialogContent>
          <DialogActions><Button onClick={() => setMfaOpen(false)}>Close</Button></DialogActions>
        </Dialog>
      )}
    </Box>
  )
}

function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ current_password: '', new_password: '', confirm: '' })
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const submit = async () => {
    if (form.new_password !== form.confirm) return setError('New passwords do not match')
    try {
      await api.post('/auth/change-password', { current_password: form.current_password, new_password: form.new_password })
      // Changing the password ends every other session; this one continues (the server sets a new session cookie).
      setDone(true)
      setError('')
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const field = (name: keyof typeof form, label: string) => (
    <TextField type="password" label={label} value={form[name]} onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
  )

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Change password</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {done ? <Alert severity="success">Password changed.</Alert> : (
            <>
              {field('current_password', 'Current password')}
              {field('new_password', 'New password (min 8, letters and digits)')}
              {field('confirm', 'Confirm new password')}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {!done && <Button variant="contained" onClick={submit}>Change</Button>}
      </DialogActions>
    </Dialog>
  )
}

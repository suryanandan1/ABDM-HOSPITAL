import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import { Box, CircularProgress } from '@mui/material'
import { api, CurrentUser } from './api'

interface AuthContextValue {
  user: CurrentUser | null
  login: (username: string, password: string, otp?: string) => Promise<void>
  logout: () => Promise<void>
  logoutEverywhere: () => Promise<void>
  refresh: () => Promise<void>
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

// Last user activity, shared by every open tab so working in one tab keeps the others signed in.
const ACTIVITY_KEY = 'abdm_last_activity'
const ACTIVITY_EVENTS = ['mousedown', 'keydown', 'wheel', 'touchstart', 'mousemove'] as const

function readActivity(): number {
  try {
    return Number(localStorage.getItem(ACTIVITY_KEY)) || Date.now()
  } catch {
    return Date.now()
  }
}

function writeActivity(time: number) {
  try {
    localStorage.setItem(ACTIVITY_KEY, String(time))
  } catch {
    // storage blocked: the timer then only counts this tab's activity
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // The session cookie cannot be read by scripts, so ask the server whether we are signed in.
    api
      .get<CurrentUser>('/auth/me')
      .then((r) => setUser(r.data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const refresh = async () => {
    const { data } = await api.get<CurrentUser>('/auth/me')
    setUser(data)
  }

  const login = async (username: string, password: string, otp?: string) => {
    const { data } = await api.post('/auth/login', { username, password, otp: otp || undefined })
    writeActivity(Date.now())
    setUser(data.user)
  }

  // Signing out must always work locally, even if the server cannot be reached.
  const logout = useCallback(async () => {
    await api.post('/auth/logout').catch(() => undefined)
    setUser(null)
  }, [])

  const logoutEverywhere = async () => {
    await api.post('/auth/logout-all').catch(() => undefined)
    setUser(null)
  }

  // Sign out after the idle limit without keyboard/mouse activity, so an unattended screen does not stay open.
  // (The server also ends idle sessions; this covers pages that keep refreshing data on their own.)
  useEffect(() => {
    if (!user) return
    let last = 0
    const onActivity = () => {
      const now = Date.now()
      if (now - last > 5000) { // write at most every 5 s
        last = now
        writeActivity(now)
      }
    }
    writeActivity(Date.now())
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true }))
    const limit = user.session_idle_minutes * 60 * 1000
    const timer = window.setInterval(() => {
      if (Date.now() - readActivity() >= limit) {
        logout().then(() => window.location.assign('/login?idle=1'))
      }
    }, 15000)
    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity))
      window.clearInterval(timer)
    }
  }, [user, logout])

  const can = (permission: string) => Boolean(user?.permissions.includes(permission))

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', height: '100vh' }}>
        <CircularProgress />
      </Box>
    )
  }
  return (
    <AuthContext.Provider value={{ user, login, logout, logoutEverywhere, refresh, can }}>{children}</AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

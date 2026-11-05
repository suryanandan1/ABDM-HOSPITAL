import { defineConfig, loadEnv, Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// Content-Security-Policy for the built app (the dev server needs inline scripts for hot reload, so it is
// added at build time only). Inline styles are allowed because MUI/Emotion inject them; documents under review
// are shown from blob: URLs. Serve the build with `frame-ancestors 'none'` as an HTTP header as well, which a
// <meta> tag cannot set. `vite preview` sends these as real headers (PRODUCTION_HEADERS); a production web server
// in front of frontend/dist should send the same ones (see README, "Serving the built frontend").
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "frame-src blob:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ')

export const PRODUCTION_HEADERS: Record<string, string> = {
  'Content-Security-Policy': `${CSP}; frame-ancestors 'none'`,
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
}

const contentSecurityPolicy = (): Plugin => ({
  name: 'content-security-policy',
  apply: 'build',
  transformIndexHtml: () => [
    { tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP }, injectTo: 'head-prepend' },
  ],
})

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react(), contentSecurityPolicy()],
    server: {
      port: 5173,
      // In development the browser calls /api on the Vite server, which forwards to FastAPI.
      proxy: { '/api': env.VITE_BACKEND_URL || 'http://127.0.0.1:8000' },
    },
    preview: {
      headers: PRODUCTION_HEADERS,
      proxy: { '/api': env.VITE_BACKEND_URL || 'http://127.0.0.1:8000' },
    },
  }
})

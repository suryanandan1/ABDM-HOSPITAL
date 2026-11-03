import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { CssBaseline, GlobalStyles, ThemeProvider, createTheme } from '@mui/material'
import App from './App'
import { AuthProvider } from './auth'

const theme = createTheme({
  palette: {
    primary: { main: '#0b6e99' },
    secondary: { main: '#2e7d32' },
    background: { default: '#f4f6f9' },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { border: '1px solid #e3e7ed' } } },
    MuiAppBar: { styleOverrides: { root: { border: 'none' } } },
    MuiDrawer: { styleOverrides: { paper: { border: 'none' } } },
    MuiTextField: { defaultProps: { size: 'small', fullWidth: true } },
    MuiButton: { styleOverrides: { root: { textTransform: 'none' } } },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {/* Printing a prescription or lab report shows only the document. */}
      <GlobalStyles styles={{ '@media print': { '.no-print': { display: 'none !important' }, body: { background: 'white' } } }} />
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
)

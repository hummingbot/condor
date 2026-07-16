import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { getCsrfToken } from './lib/csrf'

// Fetch the CSRF token once on boot (§5.5) so the first unsafe request
// doesn't pay the round-trip. Failures are retried lazily by apiFetch.
void getCsrfToken().catch(() => {})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

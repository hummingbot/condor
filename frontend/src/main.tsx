import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installErrorRecorder } from './lib/diagnostics'

// Before the first render: an error thrown on mount is exactly the one a bug
// report needs, and it happens before any component could install this.
installErrorRecorder()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

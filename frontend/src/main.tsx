import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// The two faces index.css asks for, self-hosted rather than pulled from a font
// CDN: this dashboard is run on a LAN or a VPS behind auth, and a third-party
// request on first paint is both a leak and a stall. Only the variable weight
// axis and the one mono weight the chat uses are imported — each subset is
// unicode-range gated, so a latin transcript downloads ~70KB of it.
import '@fontsource-variable/inter/wght.css'
import '@fontsource/jetbrains-mono/latin-400.css'
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

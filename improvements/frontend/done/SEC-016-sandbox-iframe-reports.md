---
id: SEC-016
title: Reports renderizados en iframe same-origin sin sandbox pueden robar el JWT de localStorage
category: security
impact: high
effort: M
risk: medium
status: done
files:
  - frontend/src/components/routines/ReportBrowser.tsx:891-896
  - frontend/src/components/routines/ReportBrowser.tsx:233
commits:
  - "e111b24 (fix) sandbox en iframe de reports en ReportBrowser (SEC-016)"
created: 2026-06-10
---

## Problema
`ReportBrowser.tsx:891-896` renderiza reports generados por routines/agentes vía
`<iframe src={`/reports/${selectedReport.filename}`} ... />` SIN atributo `sandbox`. Estos
reports son archivos HTML producidos por routines Python (autoría CodeMirror, autoría de agente)
y servidos same-origin bajo `/reports/` (`StaticFiles` mount en `condor/web/app.py:66`). Como el
iframe es same-origin y no-sandboxed, cualquier `<script>` dentro de un report ejecuta con acceso
total al origen padre: puede leer `localStorage` (incluyendo `condor_token`), llamar `/api/v1/*`
con el bearer del usuario, o exfiltrar datos. El token es un bearer de larga vida en localStorage,
así que un solo report malicioso/comprometido = account takeover. Es exactamente el tipo de HTML
de baja confianza, generado dinámicamente, que amerita sandboxing.

## Solución propuesta
Añadir un `sandbox` restrictivo al iframe. Como los reports contienen charts Plotly/JS necesitan
`allow-scripts`, pero NO deben recibir `allow-same-origin` (combinarlos anula el sandbox). Usar
`sandbox="allow-scripts allow-popups"`: deja correr el JS de los charts pero trata el frame como
contexto opaco cross-origin sin acceso a localStorage/cookies del padre ni a `/api` same-origin.
El `postMessage` de tema existente (línea 233) sigue funcionando a través del límite del sandbox
(los reports cargan Plotly desde CDN externo, sin fetch/asset same-origin). Verificar que los
charts siguen renderizando; si un report genuinamente necesita assets same-origin, servir reports
desde un origen/subdominio separado en vez de relajar el sandbox.

## Criterio de aceptación
- [ ] El iframe de reports tiene un `sandbox` que NO incluye `allow-same-origin`
- [ ] Un report con `<script>window.parent.localStorage.getItem('condor_token')</script>` no puede leer el token del padre (lanza/devuelve undefined)
- [ ] Los reports Plotly/lightweight-charts existentes siguen renderizando y el toggle de tema por postMessage sigue funcionando

## Notas
Mismo vector de riesgo que [[ARCH-010]] (centralización del token) y [[SEC-017]] (token en URL de WS).
El más alto impacto de los tres de seguridad: priorizar.

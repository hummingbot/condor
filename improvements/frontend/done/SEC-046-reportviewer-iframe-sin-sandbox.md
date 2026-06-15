---
id: SEC-046
title: ReportViewer renderiza HTML generado por routines en un iframe same-origin sin sandbox
category: security
impact: high
effort: S
risk: medium
status: done
files:
  - frontend/src/components/routines/ReportViewer.tsx:175-179
commits:
  - "6036a66 (fix) sandbox en iframe de ReportViewer (SEC-046)"
created: 2026-06-10
---

## Problema
`ReportViewer.tsx:175-179` embebe el output de reports vía `<iframe src={`/reports/${report.filename}`}>`
SIN atributo `sandbox`. El issue de iframe-sin-sandbox ya reportado en [[SEC-016]] estaba acotado a
`ReportBrowser.tsx`; `ReportViewer` es un componente **separado** (usado por los flujos de
`RoutineReports`/`Reports`) que carga los mismos documentos `/reports/*.html`, producidos por routines
Python autoradas por usuario (markdown/plotly/HTML arbitrario). Como el iframe comparte el origen de la
app y no tiene sandbox, cualquier `<script>` dentro de un report corre con acceso total al origen padre:
puede leer `localStorage` (donde vive el token `condor_token`), llamar endpoints same-origin `/api/*` con
la sesión del usuario, o navegar el top window. Un autor de routine (o cualquiera que pueda escribir un
archivo de report) puede así exfiltrar el JWT o actuar como el usuario. No hay ningún uso de `sandbox` en
todo `frontend/src` hoy.

## Solución propuesta
Agregar `sandbox` al iframe de `ReportViewer`, reflejando el hardening de [[SEC-016]]. Usar el set mínimo
de capabilities que los reports necesitan — típicamente `sandbox="allow-scripts allow-popups"` (omitiendo
deliberadamente `allow-same-origin` para que el documento enmarcado no alcance storage/cookies/DOM del
padre). A diferencia de `ReportBrowser`, `ReportViewer` NO tiene postMessage de theme-sync (no hay
`iframeRef` ni handler de `load`), así que no hay regresión de theme-sync que verificar. Confirmar que los
reports Plotly/markdown siguen renderizando bajo los flags elegidos.

## Criterio de aceptación
- [ ] El iframe de `ReportViewer` lleva un `sandbox` que omite `allow-same-origin`
- [ ] Un report con JS inline no puede leer `localStorage`/`condor_token` ni llamar `/api/*` como el usuario desde el frame
- [ ] Los reports plotly/markdown existentes siguen mostrándose correctamente dentro del iframe sandboxeado

## Notas
Segunda instancia distinta de la misma clase de vulnerabilidad que [[SEC-016]] (ReportBrowser): shippear
SEC-016 NO arregla `ReportViewer`. Conviene aplicar el mismo `sandbox` a ambos. [[ARCH-044]] (centralizar
el iframe de report) reduciría esto a un único punto a futuro.

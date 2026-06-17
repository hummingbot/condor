---
id: ARCH-044
title: Reports.tsx reimplementa el visor iframe+fullscreen en vez de reusar ReportViewer
category: architecture
impact: low
effort: M
risk: low
status: done
files:
  - frontend/src/pages/Reports.tsx:29
  - frontend/src/pages/Reports.tsx:170-176
  - frontend/src/pages/Reports.tsx:207-211
  - frontend/src/components/routines/ReportViewer.tsx
commits: [57121cc]
created: 2026-06-10
---

## Problema
`ReportViewer.tsx` es un componente reutilizable que ya encapsula el iframe de report
(`src=/reports/${report.filename}`), un toggle de fullscreen, navegación prev/next y manejo de teclado.
Sin embargo `pages/Reports.tsx` reimplementa su propio visor: estado local de fullscreen (línea 29), un
header con toggle `Maximize2`/`Minimize2` (170-176) y un `<iframe src={`/reports/${...}`}>` con switch
manual de altura (207-211). El patrón `/reports/${filename}` en iframe está ahora disperso en
`ReportViewer.tsx`, `ReportBrowser.tsx`, `Reports.tsx` (y `RoutineReports.tsx`, que sí reusa
`ReportViewer`, probando que el camino de reuso es viable), así que cambios transversales al render de
reports (sandbox, estados de loading/error) no se pueden hacer en un solo lugar.

## Solución propuesta
Reusar `ReportViewer` en `pages/Reports.tsx`, pasándole la lista de reports y `onSelect` para usar su
fullscreen/navegación incorporados, y borrar el estado de fullscreen bespoke + el markup inline del
iframe. Si el chrome de `ReportViewer` es demasiado pesado para algún caso, factorizar el elemento
iframe en sí en un mini `ReportFrame ({ filename, title, className })` que `ReportViewer` y `Reports.tsx`
rendericen.

## Criterio de aceptación
- [x] `pages/Reports.tsx` renderiza el report a través del componente compartido en vez de su propio bloque de iframe
- [x] Ver y poner en fullscreen un report desde la página Reports se comporta como antes
- [x] El patrón de iframe de report queda centralizado para futuros cambios transversales (ej. sandbox)

## Notas
El hallazgo original también incluía `AgentRoutinesTab.tsx`, pero ese archivo es **dead code** a borrar
en [[READ-047]], así que se acotó a `Reports.tsx`. Relacionado con [[SEC-016]]/[[SEC-046]] (sandbox de
iframes de reports): centralizar facilita aplicar el sandbox en un solo punto.

Implementación: se reusó `ReportViewer` directamente (sin factorizar un `ReportFrame`; no fue
necesario porque el chrome de `ReportViewer` cubre exactamente lo que la página Reports mostraba).
Beneficios heredados al reusar: el iframe sandboxeado de SEC-046 (`sandbox="allow-scripts allow-popups"`),
más navegación prev/next y atajos de teclado que la versión bespoke no tenía. La página conserva su
propio estado de empty ("Select a report to view"). El borrado ahora usa el `onDelete` de `ReportViewer`.

---
id: READ-047
title: Dead code — 5 componentes de UI del agent nunca importados por ninguna página viva
category: readability
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/components/agent/AgentExperimentsTab.tsx
  - frontend/src/components/agent/AgentSessionsTab.tsx
  - frontend/src/components/agent/AgentToolbar.tsx
  - frontend/src/components/agent/AgentFloatingPanel.tsx
  - frontend/src/components/agent/AgentRoutinesTab.tsx
commits:
  - "f3398c3 (chore) eliminar 5 componentes muertos de la UI del agent (READ-047)"
created: 2026-06-10
---

## Problema
Estos cinco componentes forman una isla aislada: la única referencia cruzada entre ellos es
`AgentFloatingPanel.tsx:4` importando el tipo `PanelId` de `AgentToolbar.tsx`. Ninguno (ni sus exports
`ExperimentsTab`, `SessionSelector`, `SessionMetricsBar`, `SessionsTab`, `AgentToolbar`,
`AgentFloatingPanel`, `AgentRoutinesTab`) es importado por ninguna página o componente vivo (grep
repo-wide confirma cero importers externos). La UI viva del agent es `AgentDetail.tsx`, que importa solo
de `AgentOverviewTab.tsx`, `AgentControls.tsx`, `AgentMarketStrip.tsx` y `SessionReviewer.tsx`. Están
commiteados (no WIP) y fueron superados por el layout basado en `SessionReviewer`. El
`AgentRoutinesTab.tsx` muerto carga un duplicado completo de la state machine de run de routine
(`RoutineCard`) y un visor iframe de reports; `AgentExperimentsTab.tsx` duplica el render de snapshots de
experimentos que ya vive en `SessionReviewer.tsx` (rama experiment ~336-378). Esta superficie muerta
infla el bundle y confunde a quien edita la UI del agent, que puede parchear archivos que nunca renderizan.

## Solución propuesta
Borrar los cinco archivos. Antes de borrar, re-correr un grep por cada símbolo exportado
(`ExperimentsTab`, `SessionSelector`, `SessionMetricsBar`, `SessionsTab`, `AgentToolbar`, `PanelId`,
`AgentFloatingPanel`, `AgentRoutinesTab`) en `src` para confirmar cero importers, y luego removerlos. Si
el markup de snapshot de experimentos o de run de routine se considera valioso, extraer el renderer
compartido a un componente reusado por `SessionReviewer` en vez de dejar el archivo de tab entero muerto.

## Criterio de aceptación
- [x] Los cinco archivos listados se eliminan del repo
- [x] `tsc`/`vite build` compila sin errores de import sin resolver
- [x] grep en `frontend/src` no encuentra referencias a `ExperimentsTab`, `SessionsTab`, `SessionSelector`, `SessionMetricsBar`, `AgentToolbar`, `AgentFloatingPanel` ni `AgentRoutinesTab`
- [x] La página de agent detail y el session reviewer siguen renderizando igual

## Notas
Interacciones a tener en cuenta al shippear:
- Hace **moot** la dedup de `useRoutineRunner` que un auditor propuso entre `RoutineDetail` y
  `AgentRoutinesTab` (la duplicación desaparece al borrar el archivo muerto). No se creó ese item.
- [[CORR-008]] y [[ARCH-035]] (resize-drag) listan `AgentFloatingPanel.tsx` como archivo corroborante;
  al borrarlo, esa referencia se vuelve innecesaria — ambos items siguen válidos por sus archivos vivos
  (`Executors`, `CreateExecutor`, `ChatPanel`). Convendría shippear READ-047 antes que esos dos.
- [[ARCH-044]] (reusar ReportViewer) ya excluyó la parte de `AgentRoutinesTab` por esta razón.

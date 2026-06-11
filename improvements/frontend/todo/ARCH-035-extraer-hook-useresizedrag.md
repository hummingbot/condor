---
id: ARCH-035
title: Handlers de resize-drag duplicados en CreateExecutor (y repetidos en varias pantallas) → extraer useResizeDrag
category: architecture
impact: low
effort: S
risk: low
status: todo
files:
  - frontend/src/pages/CreateExecutor.tsx:230-262
  - frontend/src/pages/Executors.tsx:479-487
  - frontend/src/components/chat/ChatPanel.tsx:110-114
  - frontend/src/components/agent/AgentFloatingPanel.tsx:54-58
commits: []
created: 2026-06-10
---

## Problema
`CreateExecutor.tsx` define dos handlers de pointer-drag casi idénticos, `startHDrag` (230-245) y
`startVDrag` (247-262), que difieren solo en el eje (`clientX` vs `clientY`), el clamp min/max y qué
setter llaman. Ambos agregan manualmente listeners `mousemove`/`mouseup` a `document`, setean
`document.body.style.cursor` y limpian en `mouseup`. Es boilerplate de lógica viviendo en el
componente, fácil de equivocar (el backlog ya marca una variante con fuga de listeners en
[[CORR-008]]), y el mismo patrón de resize-handle recurre en `Executors.tsx` (479-487), `ChatPanel.tsx`
(110-114) y `AgentFloatingPanel.tsx` (54-58).

## Solución propuesta
Extraer un hook reutilizable, ej. `useResizeDrag({ axis, min, max, value, onChange })`, en
`frontend/src/hooks/`, que encapsule el add/remove de listeners, el estilo de cursor y el clamping (y
garantice cleanup en unmount). Reemplazar `startHDrag`/`startVDrag` en `CreateExecutor` por dos
llamadas al hook, y reusarlo en los otros panes redimensionables.

## Criterio de aceptación
- [ ] Un único hook `useResizeDrag` encapsula el ciclo de vida de listeners `mousemove`/`mouseup` y el clamping
- [ ] Los handles horizontal y vertical de `CreateExecutor` usan el hook en vez de handlers bespoke
- [ ] Los listeners de document siempre se remueven en `mouseup` sin fuga

## Notas
Complementa [[CORR-008]] (fuga de listeners en resize-drag): el hook, con cleanup en unmount,
resuelve de raíz la clase de bug. Conviene shippear CORR-008 y este juntos o este primero.

---
id: ARCH-035
title: Handlers de resize-drag duplicados en CreateExecutor (y repetidos en varias pantallas) → extraer useResizeDrag
category: architecture
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/hooks/useResizeDrag.ts
  - frontend/src/pages/CreateExecutor.tsx:230-262
  - frontend/src/pages/Executors.tsx:479-487
  - frontend/src/components/chat/ChatPanel.tsx:110-114
commits:
  - "81b338c (refactor) extract useResizeDrag hook (ARCH-035)"
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
- [x] Un único hook `useResizeDrag` encapsula el ciclo de vida de listeners `mousemove`/`mouseup` y el clamping
- [x] Los handles horizontal y vertical de `CreateExecutor` usan el hook en vez de handlers bespoke
- [x] Los listeners de document siempre se remueven en `mouseup` sin fuga

## Notas
Complementa [[CORR-008]] (fuga de listeners en resize-drag): el hook, con cleanup en unmount,
resuelve de raíz la clase de bug. CORR-008 ya estaba cerrado; el hook preserva ese patrón
(`dragCleanup` ref + `useEffect(() => () => cleanup(), [])`) para que un unmount mid-drag siempre
desadjunte listeners y restaure `document.body` cursor/userSelect.

### Sitios y params

Se confirmaron **3 sitios vivos**, no 4: `AgentFloatingPanel.tsx` ya no existe (eliminado como
componente muerto en `f3398c3`, READ-047), así que ese cuarto sitio no aplica.

- **`CreateExecutor` startHDrag** → `{ axis: "x", value: rightPanelWidth, min: 260, max: 500,
  direction: "inverted", cursor: "col-resize" }`. Panel anclado a la derecha (arrastrar a la
  izquierda agranda), de ahí `inverted`.
- **`CreateExecutor` startVDrag** → `{ axis: "y", value: bottomPaneHeight, min: 100, max: 500,
  direction: "inverted", cursor: "row-resize" }`. Pane anclado abajo.
- **`Executors` DetailPanel** → `{ axis: "x", value: panelWidth, min: 300,
  max: () => window.innerWidth * 0.8, compute: (coord) => window.innerWidth - coord,
  cursor: "col-resize", lockUserSelect: true }`. Único que usa posicionamiento **absoluto**
  (`innerWidth - clientX`, no delta-desde-inicio) y **max dinámico**, cubiertos vía `compute` +
  `max` función. También el único que bloquea `userSelect`, vía `lockUserSelect`.
- **`ChatPanel`** → `{ axis: "x", value: width, min: 360, max: 1200, direction: "inverted" }`.
  No setea cursor en `document.body` (se omite `cursor`); usa el `isDragging` que retorna el hook
  para el styling del handle (reemplaza el `useState` local previo).

### Deviation
El hook tiene su propio estado `isDragging` (lo necesita `ChatPanel` para UI). En `Executors` el
ref `isDragging` previo solo gateaba `onMouseMove` de forma redundante (el listener ya se remueve en
mouseup); el hook no expone ese gate porque es innecesario — el comportamiento observable no cambia.

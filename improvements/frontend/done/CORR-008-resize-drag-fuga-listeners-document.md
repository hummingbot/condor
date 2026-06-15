---
id: CORR-008
title: El resize-drag del panel de detalle fuga listeners de document y corrompe cursor/userSelect global
category: correctness
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/pages/Executors.tsx:468-488
  - frontend/src/pages/CreateExecutor.tsx:243-261
  - frontend/src/components/agent/AgentFloatingPanel.tsx:57-58
commits:
  - "9266f06 (fix) limpiar listeners de resize-drag en unmount (CORR-008)"
created: 2026-06-10
---

## Problema
El handler de resize del panel de detalle (`Executors.tsx:468-488`) adjunta listeners
`mousemove`/`mouseup` a `document` dentro de `onMouseDown` y solo los remueve en `onMouseUp`.
Si el panel se desmonta a mitad de un drag (el usuario selecciona otro executor o cierra el
panel mid-drag), el `mouseup` nunca corre: los listeners quedan adjuntos a `document`, el
`onMouseMove` sigue llamando `setPanelWidth`, y `document.body.style.cursor`/`userSelect`
quedan mutados globalmente (cursor `col-resize` pegado y selección de texto deshabilitada en
toda la app hasta el próximo drag). El mismo patrón sin limpieza aparece en
`CreateExecutor.tsx:243-261` y `AgentFloatingPanel.tsx:57-58`.

> Nota del verificador: el síntoma "state update on unmounted component" está obsoleto en React 19
> (el setter es no-op silencioso); lo load-bearing es la fuga de listeners + corrupción de cursor/selección global.

## Solución propuesta
Mover el wiring del drag a un `useEffect` keyed sobre un flag de estado `isDragging`, o que
`onMouseDown` registre los listeners y un efecto los limpie en unmount. Lo más simple: guardar
los `onMouseMove`/`onMouseUp` activos en refs y añadir un
`useEffect(() => () => { remove both listeners; reset body cursor/userSelect; }, [])`, de modo
que un unmount mid-drag siempre los desadjunte y restaure `document.body`.

## Criterio de aceptación
- [x] Desmontar el panel de detalle (o el draggable de CreateExecutor) mid-drag remueve los listeners `mousemove`/`mouseup` de document
- [x] `document.body` cursor y `userSelect` se restauran aunque el drag se interrumpa por unmount
- [x] No queda cursor `col-resize` pegado ni selección de texto deshabilitada tras navegar durante un resize

## Notas
Patrón repetido en 3 archivos; conviene resolver `Executors.tsx` primero y replicar a los otros dos.

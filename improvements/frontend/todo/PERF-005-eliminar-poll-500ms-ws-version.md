---
id: PERF-005
title: useCondorWebSocket pollea ws.version cada 500ms durante toda la vida de la conexión
category: performance
impact: low
effort: S
risk: low
status: todo
files:
  - frontend/src/hooks/useWebSocket.ts:203-208
  - frontend/src/hooks/useWebSocket.ts:198
  - frontend/src/lib/websocket.ts:70-82
commits: []
created: 2026-06-10
---

## Problema
Un `setInterval` que corre cada 500ms se instala durante toda la vida del WebSocket para
pollear `ws.version` (`useWebSocket.ts:203-208`) como "fallback para reconexiones". Pero el
WS ya incrementa `version` y dispara `connectHandlers` en cada (re)conexión vía `onopen`
(`websocket.ts:70-82`), y `useCondorWebSocket` ya registra `onConnect -> setWsVersion`
(`useWebSocket.ts:198`) antes de llamar `connect()`. El callback `onConnect` ya cubre las
reconexiones; el poll de 500ms es redundante y mantiene un timer disparando continuamente
(impide que el tab haga idle, despierta el event loop sin necesidad).

## Solución propuesta
Eliminar el interval `versionPoll` y confiar solo en `ws.onConnect(() => setWsVersion(v => v + 1))`.
Si se quiere un fallback belt-and-suspenders, dispararlo desde un `connectHandler` (que ya
corre en reconexión) en vez de un poll continuo. Verificar que `CondorWebSocket` dispara
`onConnect` en cada reconexión (el `onopen` en `websocket.ts:70` ya recorre `connectHandlers`).

## Criterio de aceptación
- [ ] No queda ningún `setInterval` polleando `ws.version` en `useWebSocket.ts`
- [ ] `wsVersion` sigue incrementando en el connect inicial y en cada reconexión (simular matando/restaurando el WS)
- [ ] Los componentes que dependen de `wsVersion` siguen re-renderizando en reconexión

## Notas
Impacto bajo (CPU mínima) pero limpieza segura y de bajo riesgo.

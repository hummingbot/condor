---
id: CORR-009
title: useChatSocket muta activeSlotId desde dentro de un updater de setSlots (updater impuro, frágil en StrictMode/concurrent)
category: correctness
impact: medium
effort: S
risk: medium
status: todo
files:
  - frontend/src/hooks/useChatSocket.ts:175-193
  - frontend/src/hooks/useChatSocket.ts:471
commits: []
created: 2026-06-10
---

## Problema
El handler `session_destroyed` (`useChatSocket.ts:175-193`) primero hace
`setActiveSlotId(prev => prev === destroyedId ? null : prev)` y luego, para elegir un slot de
reemplazo, llama `setActiveSlotId(...)` desde *dentro* de un updater de
`setSlots(prev => { setActiveSlotId(...); return prev; })`. Los updaters de estado deben ser
puros; anidar un `setState` como side-effect dentro de otro updater se ejecuta dos veces bajo
StrictMode (dev) de React 18/19 y no garantiza observar el `prev` correcto bajo concurrent
rendering. Esto puede dejar `activeSlotId` apuntando a un slot destruido o hacer flicker en la
selección. `activeSlot` se deriva luego vía `slots.find(...)` (línea 471) y puede ser
transitoriamente null/stale. Además el código es redundante (179-182 ya nulifica el id activo
cuando fue destruido, y 184-192 lo re-chequea).

## Solución propuesta
Computar el próximo slot activo fuera de cualquier updater. Como `slots` tras la remoción es
determinístico, capturar las slots filtradas en un const local dentro del updater de `setSlots`
y luego llamar `setActiveSlotId(filtered[0]?.info.slot_id ?? null)` como dos updates de estado
top-level y puros. Eliminar el `setState`-dentro-de-updater por completo.

## Criterio de aceptación
- [ ] No ocurre ninguna llamada a `setState` dentro de un updater de `setSlots`/`setActiveSlotId`
- [ ] Destruir la sesión activa selecciona el primer slot restante (o null) de forma determinística, incluso bajo doble invocación de StrictMode en dev
- [ ] `activeSlot` nunca resuelve a un slot id destruido tras un evento `session_destroyed`

## Notas
Un agente de auditoría aplicó este fix durante el análisis; se revirtió para respetar el flujo
read-only. El diff propuesto ya fue validado con `tsc --noEmit` clean — usar `/ship-improvement CORR-009`
para reaplicarlo.

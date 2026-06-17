---
id: CORR-009
title: useChatSocket muta activeSlotId desde dentro de un updater de setSlots (updater impuro, frágil en StrictMode/concurrent)
category: correctness
impact: medium
effort: S
risk: medium
status: done
files:
  - frontend/src/hooks/useChatSocket.ts:175-193
  - frontend/src/hooks/useChatSocket.ts:471
commits:
  - fbf9c72
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
- [x] No ocurre ninguna llamada a `setState` dentro de un updater de `setSlots`/`setActiveSlotId`
- [x] Destruir la sesión activa selecciona el primer slot restante (o null) de forma determinística, incluso bajo doble invocación de StrictMode en dev
- [x] `activeSlot` nunca resuelve a un slot id destruido tras un evento `session_destroyed`

## Notas
### Cómo se preservó el comportamiento
El handler `session_destroyed` original era equivalente a: quitar el slot destruido de `slots`;
y si el activo era el destruido (o ya era `null`), pasar a activo el primer slot restante (o
`null` si no quedan), si no, mantener el activo. La línea 179-182 nulificaba el id y la 184-192
lo re-seleccionaba — el efecto neto es esa misma regla.

El refactor computa la lista `remaining` (slots tras la remoción) una sola vez **fuera** de
cualquier updater, leyéndola de un nuevo `slotsRef` (espejo de `slots` sincronizado en el
mismo `useEffect` que ya persistía los slots). Luego:
- `setSlots(remaining)` — set directo, sin updater impuro.
- `setActiveSlotId((cur) => cur === destroyedId || cur === null ? (remaining[0]?.info.slot_id ?? null) : cur)`
  — updater **puro**: solo deriva el próximo id de `cur` (prev) y del const local `remaining`.

Ambos setters parten de la misma `remaining`, así que la selección es internamente consistente
y determinística aunque StrictMode/concurrent invoque los updaters dos veces (no hay side
effects anidados). El `slotsRef` refleja los slots ya comiteados al momento del evento WS (cada
mensaje WS es un callback `onmessage` separado, con React flusheando y corriendo effects entre
ellos), por lo que coincide con el `prev` que recibía el `setSlots` original.

### Desviaciones respecto a la solución propuesta
La solución propuesta sugería capturar `filtered` dentro del updater de `setSlots`. Eso seguiría
implicando leer/depender del cuerpo de un updater; en su lugar se introdujo `slotsRef` para
calcular `remaining` totalmente afuera, dejando los dos setters puros. Mismo resultado
observable, sin captura desde dentro de un updater.

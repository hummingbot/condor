---
id: SEC-039
title: Los mensajes de chat persistidos a localStorage crecen sin límite ni expiración
category: security
impact: low
effort: M
risk: low
status: done
files:
  - frontend/src/hooks/useChatSocket.ts:40-50
  - frontend/src/hooks/useChatSocket.ts:465-469
commits:
  - 6ff550d
created: 2026-06-10
---

## Problema
`saveSlotMessages` (`useChatSocket.ts:40-50`) serializa el historial completo de mensajes de cada slot
a localStorage con la única condición `s.messages.length > 0` (línea 44) — sin tope de mensajes por
slot, sin tope de slots, sin expiración. El effect de persistencia (465-469) corre en cada cambio de
`slots`, y como cada `text_chunk`/`thought_chunk`/`tool_call` hace `setSlots` durante el streaming, se
re-serializa el array completo muy frecuentemente. Con el tiempo acumula toda la conversación del
trading agent (que puede incluir figuras de portfolio, balances y outputs de tools) en localStorage
plaintext, eventualmente arriesgando la cuota ~5MB (el `catch` en la línea 49 traga silenciosamente el
`QuotaExceededError`, así que la persistencia deja de funcionar sin señal).

## Solución propuesta
Capear el historial persistido (ej. mantener solo los últimos N mensajes por slot y los últimos M
slots) antes de `JSON.stringify`, y considerar recortar payloads de tool-call. Opcionalmente guardar
bajo una key versionada con timestamp y descartar entradas más viejas que un umbral al cargar. Esto
acota el crecimiento y mantiene el costo de serialización predecible. Solo recorta lo persistido; el
estado en memoria queda intacto.

## Criterio de aceptación
- [x] El historial de chat persistido está capeado a un máximo fijo de mensajes-por-slot y de slots
- [x] La serialización deja de escribir arrays no acotados en cada update
- [ ] La cuota no se agota silenciosamente por sesiones de larga duración

## Notas
Implementado en `saveSlotMessages` (`useChatSocket.ts`). Antes de `JSON.stringify` se aplican dos topes
fijos, documentados como constantes:
- `MAX_PERSISTED_SLOTS = 10` — solo se persisten los slots con mensajes más recientes (`slice(-N)` sobre
  el array de slots, que ya está ordenado por actividad).
- `MAX_PERSISTED_MESSAGES_PER_SLOT = 100` — solo se guardan los últimos 100 mensajes de cada slot.

El estado en memoria (`slots`) queda intacto; solo se recorta la copia persistida. Al cargar
(`loadSlotMessages`) no hace falta recorte adicional: lo restaurado ya viene capeado porque se guardó
capeado, y queda inmediatamente re-capeado en el siguiente save.

Desviaciones / fuera de alcance (no implementado a propósito, mínimo y correcto):
- No se agregó TTL/expiración ni key versionada con timestamp (opcional en la propuesta). Los topes de
  tamaño ya acotan el crecimiento; el tercer criterio (cuota silenciosa) queda mitigado pero el `catch`
  silencioso sigue ahí — un manejo explícito de `QuotaExceededError` sería otro item.
- No se recortan payloads individuales de tool-call (la propuesta lo menciona como "considerar").

Distinto de los otros items de `useChatSocket.ts` ([[CORR-009]] setState impuro, [[SEC-017]] JWT en
URL del WS). La re-serialización la disparan los chunks de streaming, no las teclas.

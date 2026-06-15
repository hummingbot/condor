---
id: PERF-022
title: AgentSessionContent fetchea snapshots en serie dentro de un loop (N+1, bloquea las burbujas del chart)
category: performance
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/components/agent/AgentSessionContent.tsx:169-195
commits:
  - "<pending> (perf) paralelizar fetch de snapshots con Promise.all (PERF-022)"
created: 2026-06-10
---

## Problema
En `SessionExecutors` (`AgentSessionContent.tsx:169-195`), el `queryFn` de `snapshotQueries`
recorre cada snapshot summary y hace `await api.getSnapshot` de a uno
(`for (const snap of snapshotSummaries) { await api.getSnapshot(slug, sessionNum, snap.tick) }`).
Una sesión con N snapshots emite N round-trips secuenciales, así que la latencia total es la
suma de todos los requests en vez del más lento. Los marcadores de burbuja del `ExecutorChart`
(`snapshotBubbles`, línea 197) solo aparecen cuando resuelve toda la cadena serial. Para una
sesión con 30 snapshots se serializan 30 latencias antes de renderizar las burbujas. Como está
dentro de un único `queryFn`, react-query no paraleliza.

## Solución propuesta
Reemplazar el `for await` serial por `Promise.all` sobre los snapshot summaries, mapeando cada
uno a un `try/catch` que resuelva a un `SnapshotBubble` (éxito o fallback). Esto dispara todos
los `api.getSnapshot` concurrentemente y colapsa la latencia a la del request más lento.
Mantener el mismo shape de resultado para no tocar el código downstream. Opcionalmente acotar
concurrencia si el backend es sensible.

## Criterio de aceptación
- [x] Las llamadas a `api.getSnapshot` corren concurrentemente (verificable en el network panel: requests solapados, no waterfall)
- [x] `snapshotQueries.data` sigue devolviendo un `SnapshotBubble` por snapshot en orden de tick
- [x] Un snapshot que falla sigue produciendo una burbuja fallback (tick + timestamp) sin rechazar toda la query

## Notas
El `queryKey` embebe la lista de ticks (línea 170), así que cada cambio de cantidad de snapshots
re-fetchea todo el batch — de ahí la importancia de paralelizar.

---
id: ARCH-015
title: Bloque dedup-merge de snapshots de performance repetido verbatim en useWebSocket
category: architecture
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/hooks/useWebSocket.ts:144-157
  - frontend/src/hooks/useWebSocket.ts:173-184
commits: [493ed82]
created: 2026-06-10
---

## Problema
En el handler del prefix `controller_perf` hay dos bloques que hacen exactamente lo mismo:
mergear snapshots nuevos en el cache deduplicando por `${controller_id}:${timestamp}`. El primero
para `['controller-perf-history-all', server]` (`useWebSocket.ts:144-157`) y el segundo
por-controlador para `['controller-perf-history', server, cid]` (173-184). La lógica de
merge+dedup (crear `seen` Set, recorrer e insertar si no visto) está duplicada palabra por
palabra; un fix de dedup en uno puede olvidarse en el otro.

## Solución propuesta
Extraer una helper pura
`mergeSnapshots(existing: ControllerPerformanceSnapshot[], incoming: ControllerPerformanceSnapshot[]): ControllerPerformanceSnapshot[]`
que encapsule el dedup por `controller_id:timestamp`, y usarla en ambos `setQueryData` (~144 y ~173).

## Criterio de aceptación
- [x] Existe una sola función de merge/dedup de snapshots reutilizada por ambos caches (all y per-controller)
- [x] El comportamiento de deduplicación por `controller_id:timestamp` es idéntico al actual
- [x] tsc y lint pasan

## Notas
Ambos call sites ya guardan `if (!old) return old`, así que la helper solo corre cuando `old` existe.
Sin bug funcional hoy; valor moderado de mantenibilidad.

Cierre: helper `mergeSnapshots(existing, incoming)` a module-scope en `useWebSocket.ts`, llamada
desde ambos `setQueryData`. Comportamiento idéntico (mismo orden de inserción y dedup). `commits:`
referencia el commit del código previo al amend final que inserta este hash (one-amend-stale).

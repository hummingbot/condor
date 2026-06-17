---
id: ARCH-043
title: El agrupado de executors por connector:trading_pair (chartGroups) está duplicado en tres vistas del agent
category: architecture
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/pages/AgentDetail.tsx:101-112
  - frontend/src/components/agent/AgentOverviewTab.tsx:333-344
  - frontend/src/components/agent/AgentSessionContent.tsx:203-214
commits:
  - 2bdf1f0
created: 2026-06-10
---

## Problema
El mismo `useMemo` que agrupa executors vivos por `${ex.connector}:${ex.trading_pair}` en un
`Map<string, ExecutorInfo[]>` y devuelve `Array.from(entries)` está repetido verbatim en tres archivos:
`AgentDetail.tsx:101-112`, `AgentOverviewTab.tsx:333-344` y `AgentSessionContent.tsx:203-214`. Los tres
saltean executors sin `trading_pair`, construyen la misma key, y alimentan
`group[0].connector`/`group[0].trading_pair` a `ExecutorChart`. La única diferencia es la variable de
entrada (`liveExecutors` vs `executorInfos`) y el dep array. Cualquier cambio al agrupado (ej. incluir
account, manejar connector faltante) hay que hacerlo en tres lugares.

## Solución propuesta
Agregar un helper puro, ej. `groupExecutorsByMarket(executors: ExecutorInfo[]): [string, ExecutorInfo[]][]`
en `lib/` (junto a `executor-overlays.ts` o un nuevo `lib/executors.ts`), y reemplazar los tres bodies
inline de `useMemo` por `const chartGroups = useMemo(() => groupExecutorsByMarket(liveExecutors), [liveExecutors])`.

## Criterio de aceptación
- [x] Un único helper exportado produce el agrupado `[key, ExecutorInfo[]][]` por `connector:trading_pair`, salteando executors sin `trading_pair`
- [x] `AgentDetail.tsx`, `AgentOverviewTab.tsx` y `AgentSessionContent.tsx` llaman al helper en vez de inlinear el loop del Map
- [x] Los charts de executors vivos en las vistas de agent detail/overview/session renderizan los mismos grupos que antes

## Notas
Refactor de extracción pura, bajo riesgo. `AgentSessionContent.tsx` también figura en [[PERF-022]] y
[[ARCH-033]], concerns distintos.

Helper `groupExecutorsByMarket` agregado a `frontend/src/lib/executor-overlays.ts` (en vez de un nuevo
`lib/executors.ts`), ya que ese módulo es el hogar existente de helpers de executor/chart. La guarda
`serverName` quedó en cada call site (no en el helper) para preservar el comportamiento idéntico; el
helper es puro y solo agrupa. `ExecutorInfo` quedó sin uso en `AgentDetail.tsx` y `AgentOverviewTab.tsx`
tras la extracción, así que se removió de sus imports.

---
id: PERF-003
title: TradeChart hace setData() sobre todo el array de candles en cada tick WS (redundante con el update incremental)
category: performance
impact: high
effort: M
risk: medium
status: todo
files:
  - frontend/src/components/trade/TradeChart.tsx:471-487
  - frontend/src/components/trade/TradeChart.tsx:490-507
  - frontend/src/hooks/useCandleStore.ts:58-63
  - frontend/src/lib/candle-store.ts:252-259
commits: []
created: 2026-06-10
---

## Problema
En cada mensaje WS de candle, `candleStore._notify` reconstruye y re-ordena la colección
completa (`candle-store.ts:252-259` vía `getCandles`, hasta `MAX_COLLECTION_SIZE = 2000`)
y empuja el nuevo array a estado React vía `useCandleStore` (`useCandleStore.ts:58-63`). Esa
nueva referencia `candles` dispara el efecto en `TradeChart.tsx:471-487`, que mapea TODAS
las candles y llama `series.setData(mapped)` — un reset completo del chart — en cada tick.
Mientras tanto un listener SEPARADO en `TradeChart.tsx:490-507` ya hace el `series.update()`
barato solo para la última candle. Cada tick paga ambos: un map+setData de 2000 elementos
(caro, además resetea el rango visible internamente) Y el update incremental. El `setData`
completo solo se necesita en la carga inicial y en backfills REST, no por tick.

## Solución propuesta
Hacer que el efecto de `setData` completo corra solo en cambios estructurales, no por tick.
Opciones: (a) guardar el efecto de `setData` para que solo dispare cuando cambia el COUNT
de candles o en first-load / cambio de pair-interval, dejando que el listener de la línea
490 sea dueño de los updates por tick; o (b) manejar el chart enteramente desde el listener
del candleStore (bulk `setData` en el primer no-vacío + `update()` después) y dejar de
empujar el array completo a estado React hacia un efecto de `setData`. Trackear el
length/key último renderizado en un ref para decidir bulk vs incremental.

## Criterio de aceptación
- [ ] Durante ticks en vivo, `series.setData` sobre el array completo NO se llama por tick (verificado con log en el efecto); solo corre `series.update()` por tick
- [ ] La carga inicial, el switch de pair/interval y el backfill REST siguen haciendo `setData` completo + `fitContent` una vez
- [ ] Los visuales del chart (candles, overlays, fit) no cambian

## Notas
Hot path en vivo. El backfill (`mergeCandles`, TradeChart.tsx:144) inserta al frente → el
count crece y dispara el path bulk, lo cual preserva el comportamiento.

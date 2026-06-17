---
id: PERF-003
title: TradeChart hace setData() sobre todo el array de candles en cada tick WS (redundante con el update incremental)
category: performance
impact: high
effort: M
risk: medium
status: done
files:
  - frontend/src/components/trade/TradeChart.tsx:471-487
  - frontend/src/components/trade/TradeChart.tsx:490-507
  - frontend/src/hooks/useCandleStore.ts:58-63
  - frontend/src/lib/candle-store.ts:252-259
commits: [782f79a]
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
- [x] Durante ticks en vivo, `series.setData` sobre el array completo NO se llama por tick (el efecto retorna temprano cuando `needsFullReset` es false); solo corre `series.update()` por tick vía el listener del candleStore
- [x] La carga inicial, el switch de pair/interval y el backfill REST siguen haciendo `setData` completo + `fitContent` una vez
- [x] Los visuales del chart (candles, overlays, fit) no cambian — el listener incremental (línea ~490) ya era dueño del update por tick; solo se eliminó el `setData` redundante

## Notas
**Cómo se distingue incremental vs full-reset.** Se guarda en un ref la firma del último
`setData` exitoso: `${key}|${earliestTimestamp}|${count}` (key = server:connector:pair:interval).
En cada cambio de `candles` el efecto calcula `needsFullReset` y solo entonces mapea + llama
`setData` + (en first-load) `fitContent`; si no, retorna temprano. `needsFullReset` es true cuando:
- **first-load**: no hay firma previa (`prevSig === ""`);
- **cambio de key**: pair/interval/connector/server cambió → la data pertenece a otro canal;
- **history prepend**: `candles.length > prevLen && first < prevFirst`, es decir el backfill REST
  (`mergeCandles`, ~línea 145) insertó candles más viejos al frente. `series.update()` de
  lightweight-charts solo maneja la última barra o una barra nueva más reciente, nunca insertar
  antes de la data, así que ese caso requiere `setData`.

Un tick en vivo mantiene la misma key y el mismo `first` (update de la última barra) o crece el
count agregando una barra MÁS NUEVA (`first` sin cambio) → `historyPrepended` false → no corre
`setData`; el listener del candleStore ya aplicó el `update()` barato. El efecto agrega
`server/connector/pair/interval` a sus deps (necesario para construir la key); tsc 0 errores,
eslint sin nuevos errores (solo el warning preexistente de `pricePrecision` en otro efecto).

**Fuera de alcance (no tocado):** `useCandleStore` sigue empujando el array completo a estado React
en cada tick (opción b del item habría evitado eso manejando todo desde el listener); se eligió la
opción (a) por ser el cambio mínimo y seguro. `candle-store._notify` sigue recomputando `sorted`
por tick. Ninguno de los dos es el costo dominante que reportaba el item (el map+setData de 2000).

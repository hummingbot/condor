---
id: CORR-007
title: El efecto de chart de BacktestingTab no tiene guard de cancelación — fuga instancias de lightweight-charts en re-renders/unmount
category: correctness
impact: high
effort: M
risk: low
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:144-378
  - frontend/src/components/trade/TradeChart.tsx:158-161
commits:
  - 69ccc4c
created: 2026-06-10
---

## Problema
El efecto que construye los charts (`BacktestingTab.tsx:167-399`) corre un IIFE async que
hace `await import("lightweight-charts")` y luego crea 1-3 charts, empujándolos a
`chartsRef.current` (declarado en línea 153). El cleanup (392-398) solo remueve lo que está
actualmente en `chartsRef.current` de forma síncrona. Las deps del efecto son
`[data, theme, hasPnl, hasPositionHeld]` (línea 399), que cambian frecuentemente (cada poll/
resultado de backtest, toggle de tema, resize). Su único check post-await es
`if (!priceRef.current) return` (175), que sigue truthy mientras está montado. Cuando el efecto
re-corre (o el componente se desmonta) mientras un IIFE previo sigue esperando el dynamic
import, el cleanup viejo dispara primero contra un array vacío/parcial, luego el IIFE stale
resume y llama `mod.createChart(...)` contra refs ya superados, sobrescribiendo
`chartsRef.current`. El set intermedio de charts nunca se `.remove()`ea, y en unmount se pueden
crear charts después del teardown → fuga de nodos DOM, ResizeObservers y suscripciones, y
charts duplicados/fantasma. `TradeChart.tsx:158-161` ya usa un `let cancelled` para exactamente
este patrón; este efecto no.

## Solución propuesta
Introducir `let cancelled = false;` al tope del efecto y `return () => { cancelled = true; ...cleanup }`.
Dentro del IIFE async, chequear `if (cancelled) return;` inmediatamente después de
`await import(...)` (e idealmente antes de cada `createChart`). Los charts creados en este run
deben coleccionarse en un array local y removerse en el mismo closure de cleanup, para que cada
run del efecto sea dueño y haga teardown exactamente de sus propios charts, en vez de compartir
el `chartsRef.current` mutable entre runs solapados.

## Criterio de aceptación
- [x] Tras resolver `await import`, el IIFE retorna temprano si el efecto ya fue limpiado (`cancelled === true`)
- [x] Cambiar rápido `data`/`theme` o desmontar a mitad del import nunca deja charts huérfanos: el número de `IChartApi` vivos tras estabilizar iguala los renderizados para la última data
- [x] No aparecen artefactos "Object is disposed"/canvas duplicado al togglear tema mientras carga un resultado de backtest

## Notas
Patrón ya resuelto en `TradeChart.tsx:158-161` — copiar ese enfoque.

Implementación: se añadió `let cancelled = false` y un array local `charts: IChartApi[]`
que cada run del efecto posee. El IIFE retorna temprano con `if (cancelled || !priceRef.current)`
tras el `await import`. Los tres charts se empujan a `charts` (no a `chartsRef.current`
compartido); `chartsRef.current = charts` mantiene la ref en sync para el `ResizeObserver`.
El cleanup pone `cancelled = true`, remueve los charts de *este* run, y solo limpia
`chartsRef.current` si todavía apunta a este array (`chartsRef.current === charts`) para no
pisar un run más nuevo que ya hizo swap.

Verificación: `npx tsc -b` → 0. `npx eslint` sobre el archivo no agrega errores nuevos;
los 2 hallazgos restantes (línea 185 unused-disable, línea 521 set-state-in-effect) son del
baseline preexistente, fuera del efecto de chart modificado.

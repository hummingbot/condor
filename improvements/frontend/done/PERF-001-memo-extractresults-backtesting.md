---
id: PERF-001
title: Memoizar extractResults() en BacktestingTab — se reparsea el payload completo en cada render
category: performance
impact: high
effort: S
risk: low
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:594
  - frontend/src/pages/tabs/BacktestingTab.tsx:598
  - frontend/src/pages/tabs/BacktestingTab.tsx:1138
commits:
  - "2bab052 (perf) memoizar extractResults en BacktestingTab (PERF-001)"
created: 2026-06-10
---

## Problema
`extractResults()` (definida en `BacktestingTab.tsx:1138`) se invoca incondicionalmente
en el cuerpo del render en las líneas 594 (`selectedTask`) y 598 (`pinnedTask`). La
función mapea arrays de potencialmente miles de candles (1178/1193/1206), `pnl_timeseries`
(1220), `executors` (1234) y `position_held_timeseries` (1254) a objetos nuevos. Como
corre en render, **re-parsea el resultado completo del backtest en CADA re-render**: cada
tecla en el formulario de config (`configSearch` onChange 691, `tradeCost` 812, date
pickers 749/756), cada aparición del toast (efecto 542-547) y cada poll de 2s de la task
seleccionada mientras corre un backtest (`refetchInterval` 519-523). La variante pinned
(598) duplica el costo. No hay React Compiler en el build (verificado en vite.config /
package.json), así que nada lo memoiza automáticamente. El output solo depende de
`selectedTask.result` / `pinnedTask.result`.

## Solución propuesta
Envolver ambas llamadas en `useMemo` keyed sobre la referencia cruda del resultado:
`const processed = useMemo(() => rawTaskResults ? extractResults(rawTaskResults) : null, [rawTaskResults]);`
e igual para `pinnedProcessed` keyed sobre `pinnedRawResults`. react-query devuelve una
referencia estable de `data` entre renders cuando no cambia, así que el parse solo se
re-ejecuta cuando llega data nueva de la task.

## Criterio de aceptación
- [x] `extractResults` corre solo cuando cambia la referencia de `selectedTask.result` (o `pinnedTask.result`), verificable con un `console.count` temporal mientras se tipea en el formulario
- [x] Tipear en los inputs de config del backtest no dispara re-parseo de candles/executors
- [x] El output de chart y métricas es idéntico al anterior para la misma task

## Notas
Pre-requisito natural de [[ARCH-009]] (extraer `extractResults` a `lib/backtest.ts`) y
de [[ARCH-011]] (extraer un hook `useBacktest`). Se puede hacer de forma independiente.

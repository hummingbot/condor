---
id: ARCH-012
title: Mover el parsing del resultado de backtest (extractResults, ~150 líneas) a lib/ con tests
category: architecture
impact: medium
effort: M
risk: low
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:1138
  - frontend/src/pages/tabs/BacktestingTab.tsx:69-75
commits:
  - 563b1dc
created: 2026-06-10
---

## Problema
`BacktestingTab.tsx:1138` define `extractResults`, ~150 líneas de normalización pura que
absorben la variabilidad del backend: distintos shapes de `results` vs raíz, búsqueda de
métricas por múltiples aliases (`net_pnl_quote`/`net_pnl`/`total_pnl`/`pnl`, `sharpe_ratio`/
`sharpe`, `total_executors`/`total_trades`/`trade_count`, en 1155-1165), y reconstrucción de
candles desde `processed_data` tanto en formato array-de-objetos como columnar (timestamp/open/
high/low/close como arrays paralelos, 1176-1199). Es un anti-corruption layer sin estado de UI,
pero vive al fondo de un archivo de 1767 líneas, no se puede testear de forma aislada y no es
reutilizable por otras vistas que consuman el mismo `task.result`.

## Solución propuesta
Extraer `extractResults` (y los helpers de fecha `dateToTs`/`toDateInputValue` de 69-75) a un
módulo `lib/backtest.ts` junto con los tipos `BacktestData`/`CandleData`. Dejar el componente
consumiendo `extractResults(rawTaskResults)` importado. Añadir un test unitario que cubra los
dos shapes de candles (columnar y array-de-objetos) y los aliases de métricas, ya que es código
frágil de mapeo de API. (El verificador notó que la función con su dependencia `normalizeSide`
realmente abarca 1138-1297.)

## Criterio de aceptación
- [x] `extractResults` vive en `lib/` y `BacktestingTab.tsx` lo importa en vez de definirlo
- [x] Los tipos `BacktestData`/`CandleData` se exportan desde el mismo módulo `lib/`
- [ ] Hay al menos un test unitario para `extractResults` cubriendo formato columnar y array-de-objetos de candles
- [x] La pestaña de backtesting muestra métricas y candles igual que antes

## Notas
Combina bien con [[PERF-001]] (memoizar `extractResults`) y [[ARCH-013]] (extraer `useBacktest`).
Hacer la extracción a `lib/` antes facilita los otros dos.

### Cierre (implementación)
- Extraído a `frontend/src/lib/backtest.ts` (nombre del módulo según md, no el ejemplo
  `backtestResults.ts` del prompt). Se movieron `extractResults`, su helper `normalizeSide`
  y los 5 tipos (`CandleData`, `ExecutorData`, `PnlTimeseriesPoint`, `PositionHeldPoint`,
  `BacktestData`) — todos exportados. `BacktestingTab.tsx` ahora los importa.
- `extractResults` no dependía de formatters/`tsToSeconds` ni de los helpers de fecha
  `dateToTs`/`toDateInputValue`; estos últimos los usa el formulario del componente, no el
  parser, así que se dejaron en su sitio para minimizar el cambio (la referencia 69-75 del md
  era a líneas de una versión anterior del archivo).
- **Test unitario: diferido.** El repo no tiene test runner configurado (`package.json` solo
  expone `dev`/`build`/`lint`/`preview`; no hay vitest/jest). Añadir un framework de tests
  sería scope creep de infra. La función quedó pura y aislada (testeable), pero el checkbox del
  test se deja sin marcar hasta que exista un runner.
- Verificación: `npx tsc -b` → exit 0. `npx eslint` sobre los archivos cambiados no agrega
  errores nuevos (el único error/warning en `BacktestingTab.tsx` es preexistente en el efecto
  de `setSelectedTaskId`, solo se desplazó de línea; `lib/backtest.ts` limpio).

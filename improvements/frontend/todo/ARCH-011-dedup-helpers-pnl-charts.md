---
id: ARCH-011
title: Duplicación íntegra de formatTime, formatDateTime y positionQuoteValue entre los dos gráficos de PNL
category: architecture
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/components/bots/ControllerPnlChart.tsx:25-55
  - frontend/src/components/bots/AggregatedPnlChart.tsx:26-60
commits: []
created: 2026-06-10
---

## Problema
`ControllerPnlChart.tsx:25-55` y `AggregatedPnlChart.tsx:26-60` contienen copias byte-a-byte
de `formatTime`, `formatDateTime`, `toMs` y `positionQuoteValue`. Esta última es lógica de
negocio no trivial: deriva el valor neto en quote de `positions_summary` usando cadenas de
fallback `amount||net_amount_base`, `breakeven_price||entry_price||current_price`,
`side||position_side`, y signo según buy/sell/short. Además las interfaces `DataPoint`
(`ControllerPnlChart.tsx:48-55`) y `AggPoint` (`AggregatedPnlChart.tsx:53-60`) son idénticas en
forma. Cualquier corrección a la lógica de notional/side (un nuevo campo de precio o un nuevo
alias de side del backend) hay que aplicarla en dos lugares y es fácil que diverjan
silenciosamente (corrompiendo la serie de posición en solo uno de los charts).

## Solución propuesta
Mover `positionQuoteValue` a `lib/` (ej. `lib/positions.ts` o `lib/executor-overlays.ts` que
ya maneja overlays de executors) y `formatTime`/`formatDateTime`/`toMs` a `lib/formatters.ts`
(junto a `formatAge`/`formatPrice`). Unificar `DataPoint`/`AggPoint` en un único tipo
`PnlChartPoint` exportado. Importar desde ambos componentes.

## Criterio de aceptación
- [ ] `positionQuoteValue` existe una sola vez en `lib/` y ambos charts lo importan
- [ ] `formatTime`/`formatDateTime` viven en `lib/formatters.ts` y no están duplicados en los charts
- [ ] Existe un único tipo de punto de PNL reutilizado por ambos componentes
- [ ] Los gráficos de PNL por controller y agregado renderizan igual que antes

## Notas
Refactor de extracción pura, bajo riesgo. `formatTime`/`formatDateTime` solapan con la duplicación
de [[ARCH-011]]... ver también [[READ-019]] (`tsToSeconds`) que toca la misma familia de helpers de tiempo.

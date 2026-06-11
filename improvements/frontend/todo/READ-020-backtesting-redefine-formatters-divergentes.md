---
id: READ-020
title: BacktestingTab redefine helpers de formato ya exportados por lib/formatters.ts (formatPct diverge sutilmente)
category: readability
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:31-51
  - frontend/src/lib/formatters.ts:71-74
commits: []
created: 2026-06-10
---

## Problema
`BacktestingTab.tsx` define localmente `formatUsd` (31-39), `formatPnl` (41-43), `pnlColor` (45-47)
y `formatPct` (49-51), pese a que `lib/formatters.ts` ya exporta exactamente estos helpers
(`formatUsd`→`formatCurrency`, `formatPnl`→`formatCurrencyPnl`, `pnlColor`, `formatPct`). Los
primeros tres son byte-idénticos al export central. El detalle peligroso es `formatPct`: la copia
local devuelve `'+0.00%'` para `val=0`, mientras `lib/formatters.ts:71-74` devuelve `'—'` para 0 —
o sea la duplicación esconde una inconsistencia de comportamiento entre pantallas. El archivo ni
siquiera importa de `@/lib/formatters`.

> Nota del verificador: hoy todos los call sites de `formatPct` en BacktestingTab están guardados por
> un ternario de truthiness (1372/1392/1627/1628), así que 0 nunca llega a `formatPct` y el render
> actual es idéntico. Es cleanup DRY, no un bug user-visible — pero conviene decidir conscientemente
> el caso 0%.

## Solución propuesta
Eliminar las 4 funciones locales en `BacktestingTab.tsx` y reemplazarlas por
`import { formatUsd, formatPnl, pnlColor, formatPct } from "@/lib/formatters"`. Decidir
explícitamente el comportamiento de `formatPct(0)` (alinear con el central `'—'` es el mejor default
y matchea los guards existentes).

## Criterio de aceptación
- [ ] `BacktestingTab.tsx` ya no define `formatUsd`/`formatPnl`/`pnlColor`/`formatPct` localmente e importa esos símbolos de `@/lib/formatters`
- [ ] El render de PnL/USD/porcentajes en la tab de backtesting es idéntico salvo el caso 0% que queda decidido conscientemente
- [ ] tsc y lint pasan sin warnings de import sin usar

## Notas
Reduce superficie del archivo de 1767 líneas; combina con [[ARCH-012]]/[[ARCH-014]].

---
id: READ-020
title: BacktestingTab redefine helpers de formato ya exportados por lib/formatters.ts (formatPct diverge sutilmente)
category: readability
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:31-51
  - frontend/src/lib/formatters.ts:71-74
commits: [0680716]
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
- [x] `BacktestingTab.tsx` ya no define `formatUsd`/`formatPnl`/`pnlColor`/`formatPct` localmente e importa esos símbolos de `@/lib/formatters`
- [x] El render de PnL/USD/porcentajes en la tab de backtesting es idéntico salvo el caso 0% que queda decidido conscientemente
- [x] tsc y lint pasan sin warnings de import sin usar

## Notas
Reduce superficie del archivo de 1767 líneas; combina con [[ARCH-012]]/[[ARCH-014]].

### Reconciliación de `formatPct` (divergencia documentada)
- **Divergencia**: la copia local devolvía `'+0.00%'` para `val=0`; el central
  (`lib/formatters.ts:71-74`) devuelve `'—'` para `!val` (0/NaN/undefined).
- **Decisión**: se adopta el central (`'—'` para 0). Es seguro porque **todos** los call sites de
  `formatPct` en BacktestingTab guardan 0 con un ternario de truthiness
  (`data.netPnlPct ? formatPct(...) : '—'` en 1380/1400/1635/1636), así que `0` nunca llega a
  `formatPct` y el render es **byte-idéntico**. Además el `—` central matchea el fallback que ya
  usaban esos guards, evitando cualquier `'+0.00%'` accidental futuro. No se introdujo un parámetro:
  no hay necesidad real porque ningún call site quiere `'+0.00%'` para 0.
- **`formatUsd`/`formatPnl`/`pnlColor`**: byte-idénticos al central en la ruta `$` (la rama extra
  `< 0.01 && val !== 0` del central solo aplica a símbolos no-`$`), sin cambio de comportamiento.
- Verificación: `npx tsc -b` → exit 0; `npx eslint` sobre el archivo no agrega errores nuevos
  (los 2 hallazgos restantes —`set-state-in-effect` en :516 y `unused eslint-disable` en :183— son
  preexistentes y ajenos a este cambio).

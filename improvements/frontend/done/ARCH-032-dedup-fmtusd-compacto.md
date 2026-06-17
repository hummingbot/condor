---
id: ARCH-032
title: Formateador compacto de USD (fmtUsd) re-implementado en charts pese a existir en lib/formatters.ts
category: architecture
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/components/agent/AgentSessionContent.tsx:287
  - frontend/src/components/trade/TradeChart.tsx:357
  - frontend/src/components/charts/ExecutorChart.tsx:257
  - frontend/src/lib/formatters.ts
commits: [f427c8c]
created: 2026-06-10
---

## Problema
`lib/formatters.ts` ya exporta `formatCurrency`/`formatUsd` con la lógica compacta exacta
(`>= 1_000_000 → .toFixed(2)+"M"`, `>= 10_000 → (v/1000).toFixed(1)+"K"`, else `$X.XX`), pero esa
misma lógica está re-implementada como un `fmtUsd` local byte-idéntico en al menos 3 componentes que
ni siquiera importan de `lib/formatters`: `AgentSessionContent.tsx:287`, `TradeChart.tsx:357` y
`ExecutorChart.tsx:257`. Son copias exactas que pueden derivar y obligan a tocar varios archivos
ante un cambio de política de formato.

> Nota del verificador: el hallazgo original citaba 8 sitios, pero la mayoría (`TradeBottomPane`
> `formatBalance`, `CustomInfoEvolution` `formatCompact`, `OrderBook`, `RecentTrades`) son
> formateadores genéricos DISTINTOS (sin `$`, otros thresholds) → fuera de scope. La copia de
> `BacktestingTab.tsx:32` ya está cubierta por [[READ-020]]. Quedan estos 3 duplicados exactos.

## Solución propuesta
Reemplazar las definiciones locales de `fmtUsd` por imports de `formatCurrency`/`formatUsd` desde
`lib/formatters.ts`. Los tooltips usan strings JS planos, así que el import es trivial. Si un chart
genuinamente necesita un redondeo distinto, agregar UNA variante nombrada (ej. `formatCompactUsd`) a
`lib/formatters.ts` y reusarla en vez de re-declarar por componente.

## Criterio de aceptación
- [x] Ningún componente de los 3 define un `fmtUsd` local; todos usan helpers de `lib/formatters`
- [x] Los valores USD compactos renderizan idénticos en Agent session, tooltip de TradeChart y tooltip de ExecutorChart
- [x] Cambiar la política de redondeo K/M requiere editar solo `lib/formatters.ts`

## Notas
Complementa [[READ-020]] (formatters duplicados en BacktestingTab); no re-tocar ese archivo aquí.

### Reconciliación (desviación del enunciado original)
El md afirmaba que el `fmtUsd` local era "byte-idéntico" a `formatCurrency`/`formatUsd`. **No lo es**
en el tramo `< 10_000`:
- `fmtUsd` local: `"$" + v.toFixed(2)` → `1234.5` ⇒ `"$1234.50"` (sin separador de miles); `-50` ⇒ `"$-50.00"`.
- `formatCurrency` (rama `$`): `toLocaleString("en-US", {style:"currency"})` → `1234.5` ⇒ `"$1,234.50"`
  (con coma); `-50` ⇒ `"-$50.00"` (signo antes del `$`).

Reusar `formatCurrency`/`formatUsd` habría **cambiado silenciosamente** los valores renderizados
(separador de miles y posición del signo). Para no alterar lo que ve el usuario, se extrajo el helper
exacto como variante nombrada **`formatCompactUsd`** en `lib/formatters.ts` (replica byte-a-byte la
lógica de los 3 locales) y se importó en los 3 componentes. Así se deduplica sin cambio de comportamiento;
las ramas K/M ya eran idénticas. Verificado: `npx tsc -b` = 0; eslint sin errores nuevos (baseline 1 error/2 warnings preexistentes, sin relación con estos cambios).

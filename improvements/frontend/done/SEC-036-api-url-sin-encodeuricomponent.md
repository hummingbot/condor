---
id: SEC-036
title: api.ts interpola server/connector/pair/configId en URLs sin encodeURIComponent
category: security
impact: medium
effort: M
risk: low
status: done
files:
  - frontend/src/lib/api.ts:648
  - frontend/src/lib/api.ts:848
  - frontend/src/lib/api.ts:864
  - frontend/src/lib/api.ts:885
  - frontend/src/lib/api.ts:897
commits:
  - "5bf434a (fix) encodeURIComponent en URLs de /servers/* (SEC-036)"
created: 2026-06-10
---

## Problema
Toda la familia `/api/v1/servers/${server}/...` interpola `server`, `connector`, `pair`, `botId`,
`configId`, `controllerType`, `controllerName` directo en el string de URL sin codificar, mientras la
familia paralela `/api/v1/settings/...` SÍ usa `encodeURIComponent(server)` consistentemente (comparar
1153-1223 vs 636-897). Casos concretos alcanzables: `getPrice` (`api.ts:864`,
`connector=${connector}&trading_pair=${pair}`), `getOrderBook` (885), `getCandles` (897),
`clearPositionHeld` (848, `${connector}/${pair}` crudo en el PATH), `getBot` (648, `${botId}`). `pair`
y `connector` llegan acá desde el datafeed de TradingView (`tradingview-datafeed.ts:157,197`) y las
páginas de trade, así que un valor con `#`, `?`, `&` o espacio corrompe silenciosamente el request
(los params se filtran al campo equivocado), y un `/` en un identificador habilita manipulación de path
contra el backend.

## Solución propuesta
Envolver cada segmento dinámico interpolado y valor de query en `encodeURIComponent`, matcheando la
convención que ya usan los endpoints `/settings/`. Preferir construir query strings con
`URLSearchParams` (como ya se hace en `api.ts:823` para `getExecutorsPage`) en vez de concatenación
manual `?a=${a}&b=${b}`, para que la codificación sea automática y el codebase sea consistente.

## Criterio de aceptación
- [ ] Todos los segmentos de path y valores de query en llamadas `/api/v1/servers/*` están codificados (`encodeURIComponent` o `URLSearchParams`)
- [ ] Un valor de `pair`/`connector` con `#`, `&` o espacio produce una URL de request correctamente codificada
- [ ] No quedan interpolaciones crudas `${connector}`/`${pair}`/`${configId}`/`${botId}` en query strings o paths de `api.ts`

## Notas
Correctness + defense-in-depth. Nombres de función en el hallazgo original ligeramente off
(`stopExecutorPosition`→`clearPositionHeld`, `getBotDetail`→`getBot`), pero las líneas son correctas.

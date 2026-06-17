---
id: SEC-018
title: Los tooltips de los charts inyectan strings del backend en innerHTML sin escapar
category: security
impact: low
effort: M
risk: low
status: done
files:
  - frontend/src/components/trade/TradeChart.tsx:364
  - frontend/src/components/trade/TradeChart.tsx:389-406
  - frontend/src/components/charts/ExecutorChart.tsx:287-304
  - frontend/src/components/charts/ExecutorChart.tsx:724
commits:
  - 0e3f5b6
created: 2026-06-10
---

## Problema
Los tooltips de los charts construyen HTML concatenando strings derivados del backend directo en
`innerHTML` sin escapar. En `TradeChart.tsx:389-406` los valores `o.side`, `o.status`, `o.type` y
`o.closeType` se interpolan crudos, y `addRow` (línea 364) interpola `value` crudo —
`String(cfg.amount)` (381) fluye sin escapar. El mismo patrón está en `ExecutorChart.tsx:287-304`
(`o.status`, `o.type`, `o.side`, `o.closeType`). Estos campos vienen del backend Hummingbot
(declarados como `string`/`Record<string, unknown>` en `api.ts`, no enums forzados por compilador),
así que el riesgo práctico es bajo (normalmente enum-like), pero es un sink sin escapar: cualquier
backend que alguna vez exponga un string influenciado por atacante (un connector/pair/`custom_info`
derivado de datos on-chain o de terceros) en uno de estos campos produce DOM XSS stored en el origen
padre. `ExecutorChart.tsx:724` ya HTML-escapa `agentResponse`, mostrando que el codebase conoce el
riesgo pero lo aplica inconsistentemente.

> Nota del verificador: `o.side` ya se normaliza a `"buy"|"sell"` vía `normSide()` en
> executor-overlays.ts antes de llegar al overlay, así que esa porción no es explotable hoy. Esto es
> hardening defense-in-depth, no XSS externamente alcanzable actualmente.

## Solución propuesta
Escapar todos los valores dinámicos antes de asignar a `innerHTML`, o construir el tooltip con DOM
APIs (`createElement`/`textContent`) en vez de template strings. Añadir un pequeño helper
`escapeHtml()` (reemplazar `&<>"'`) y envolver `o.side`/`o.status`/`o.type`/`o.closeType` y los
argumentos `value`/`label` de `addRow` en ambos `TradeChart.tsx` y `ExecutorChart.tsx`, matcheando
el escaping que ya se hace en `ExecutorChart.tsx:724`.

## Criterio de aceptación
- [x] Todos los strings dinámicos interpolados en el `innerHTML` de tooltips en `TradeChart.tsx` y `ExecutorChart.tsx` están HTML-escapados (o renderizados vía `textContent`)
- [x] Un valor de campo con `<img src=x onerror=alert(1)>` se renderiza como texto inerte en el tooltip, no como markup
- [x] Los tooltips siguen mostrando side/status/type/closeType y filas de detalle correctamente

## Notas
Impacto bajo (no alcanzable externamente hoy) pero fix barato y no-breaking; el codebase ya tiene el
patrón en la línea 724.

### Implementación
Se añadió un helper `escapeHtml()` en `frontend/src/lib/formatters.ts` (reemplaza `& < > " '`) y se
reutilizó en ambos charts. Valores escapados antes de `innerHTML`:
- `TradeChart.tsx` / `ExecutorChart.tsx`: `o.side`, `o.status`, `o.type` (vía `.toUpperCase()`),
  `o.closeType`, `o.executorId` (slice mostrado) y los argumentos `label`/`value` de `addRow`
  (este último cubre `String(cfg.amount)` y demás detalle proveniente del backend).
- Los valores numéricos/conocidos-seguros (precios formateados, PnL, %, colores de tema) se dejaron
  sin tocar. El texto visible es idéntico al previo.
- La línea 724 de `ExecutorChart.tsx` (snapshot del trading agent) ya escapaba con `.replace(/</g…)`;
  se dejó como estaba (fuera del scope de side/status/type/closeType).

Verificación: `npx tsc -b` → exit 0. `npx eslint` de los 3 archivos → 0 errores, 1 warning
preexistente (`react-hooks/exhaustive-deps` en TradeChart:442, no relacionado con este cambio).

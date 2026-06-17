---
id: READ-040
title: pnlColor local en ControllerBrowser.tsx duplica verbatim el export de lib/formatters
category: readability
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/components/bots/ControllerBrowser.tsx:27-29
  - frontend/src/lib/formatters.ts:29-31
commits:
  - "0c23888 (refactor) dedup pnlColor in ControllerBrowser"
created: 2026-06-10
---

## Problema
`ControllerBrowser.tsx:27-29` define `function pnlColor(val)` que devuelve
`val >= 0 ? 'var(--color-green)' : 'var(--color-red)'`, byte-a-byte idéntico al `pnlColor` ya exportado
en `lib/formatters.ts:29-31`. El archivo ya importa otros helpers (`formatCurrencyVolume`,
`formatCurrencyPnl`) de `@/lib/formatters` (línea 22), así que la copia local es pura duplicación que
puede derivar del mapeo canónico de colores CSS-variable. Se usa en 7 call sites inline (350, 502, 508,
514, 521, 596, 602). (`ArchivedBotsTab`/`BotRunsTab` definen variantes con clases Tailwind, genuinamente
distintas y fuera de scope.)

## Solución propuesta
Borrar el `pnlColor` local en `ControllerBrowser.tsx` e importar `pnlColor` de `lib/formatters` junto a
los imports existentes.

## Criterio de aceptación
- [x] `ControllerBrowser` importa `pnlColor` de `lib/formatters` en vez de definirlo
- [x] No queda definición local de `pnlColor` (CSS-variable) en `ControllerBrowser.tsx`

## Notas
Mismo género que [[READ-020]] (formatters locales en BacktestingTab), distinto archivo. Implementaciones
idénticas → comportamiento sin cambios.

Implementado tal cual la solución propuesta. Nota: `/improvements/` está en `.gitignore`, pero los items
ya versionados en `done/` están force-added; se usó `git add -f` para mantener la consistencia. tsc -b
pasa en 0; eslint del archivo conserva 1 error preexistente (`set-state-in-effect` en línea ~95, no
relacionado) y no introduce nuevos.

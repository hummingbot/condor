---
id: READ-021
title: Portfolio redefine isExecutorActive localmente pese a existir el export central
category: readability
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/pages/Portfolio.tsx:71-73
  - frontend/src/lib/formatters.ts:76-78
commits:
  - 7a9aa42 (refactor) use shared isExecutorActive in Portfolio
created: 2026-06-10
---

## Problema
`Portfolio.tsx:71-73` define `isExecutorActive(status) => status === 'active' || status === 'running'`,
idéntico al export ya existente en `lib/formatters.ts:76-78`. `Portfolio.tsx` ya importa de
`@/lib/formatters` en la línea 28, así que la copia local es redundante y duplica la regla de "qué
cuenta como executor activo", que `Executors.tsx` (línea 36) y `TradeBottomPane.tsx` (línea 14) sí
consumen del módulo central. Portfolio es el único outlier; si la definición de "activo" cambia
(ej. un nuevo status), el KPI `activeExecutorCount` de Portfolio (usado en `Portfolio.tsx:893`)
quedaría silenciosamente desincronizado de todas las demás vistas.

## Solución propuesta
Borrar la función local en `Portfolio.tsx:71-73` y agregar `isExecutorActive` al import existente
desde `@/lib/formatters`.

## Criterio de aceptación
- [x] `Portfolio.tsx` no define `isExecutorActive` localmente e importa la versión central
- [x] El cálculo de executors activos en Portfolio es idéntico al actual
- [x] tsc y lint pasan

## Notas
Implementaciones idénticas → comportamiento runtime sin cambios. Cleanup trivial de bajo riesgo.

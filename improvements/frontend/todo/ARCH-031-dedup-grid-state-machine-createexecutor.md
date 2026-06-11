---
id: ARCH-031
title: La state machine del grid executor está duplicada entre CreateExecutor y CreateGridExecutor
category: architecture
impact: high
effort: M
risk: medium
status: todo
files:
  - frontend/src/pages/CreateExecutor.tsx:44-154
  - frontend/src/pages/CreateGridExecutor.tsx:17-154
commits: []
created: 2026-06-10
---

## Problema
`CreateExecutor.tsx:44-154` y `CreateGridExecutor.tsx:17-154` contienen una copia casi idéntica de
toda la state machine del grid: `GRID_DEFAULTS`/`DEFAULTS` (los 24 campos de `GridState`),
`GRID_PERSISTED_FIELDS`/`PERSISTED_FIELDS`, `loadGridDefaults`/`loadSavedDefaults`,
`saveGridDefaults`/`saveDefaults` (mismo `STORAGE_KEY "condor_grid_defaults"`), `gridReducer`
(`SET_FIELD`/`SET_CONNECTOR`/`SET_PAIR` con la misma coerción spot-leverage), más `INTERVALS` y
`LOOKBACK_OPTIONS` (byte-idénticos). Cualquier cambio a defaults o persistencia (ej. agregar un
campo) hay que hacerlo en dos lugares y derivará silenciosamente. `CreateExecutor` ya importa
`GridState`/`GridAction`/`isSpotConnector` de `CreateGridExecutor` pero re-declara el resto. Además
`loadGridDefaults` (`CreateExecutor.tsx:85`) tiene dead code:
`const merged = raw ? { ...GRID_DEFAULTS } : { ...GRID_DEFAULTS }` (ambas ramas idénticas).

## Solución propuesta
Extraer la state machine (`DEFAULTS`, `PERSISTED_FIELDS`, load/save, `gridReducer`, `INTERVALS`,
`LOOKBACK_OPTIONS`, `isSpotConnector`, `GridState`, `GridAction`) a un módulo dedicado
`frontend/src/lib/gridExecutor.ts` (o un hook `useGridExecutorState`). Importarlo desde ambas
páginas. Eliminar las declaraciones duplicadas de `CreateExecutor.tsx` y corregir el ternario muerto.

## Criterio de aceptación
- [ ] Defaults, lista de persisted-fields, load/save y `gridReducer` existen en exactamente un módulo importado por ambas páginas
- [ ] Agregar un campo nuevo a `GridState` requiere editar solo un archivo
- [ ] `loadGridDefaults` ya no contiene el ternario de ramas idénticas
- [ ] Ambas páginas Create siguen creando grid executors con comportamiento idéntico

## Notas
Habilita [[ARCH-033]]: al mover `GridState`/`GridAction` a `lib/`, `GridConfigPanel` deja de
importar tipos desde `@/pages/CreateGridExecutor`.

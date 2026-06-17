---
id: ARCH-031
title: La state machine del grid executor está duplicada entre CreateExecutor y CreateGridExecutor
category: architecture
impact: high
effort: M
risk: medium
status: done
files:
  - frontend/src/pages/CreateExecutor.tsx:44-154
  - frontend/src/pages/CreateGridExecutor.tsx:17-154
commits: [8e11b9e]
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
- [x] Defaults, lista de persisted-fields, load/save y `gridReducer` existen en exactamente un módulo importado por ambas páginas
- [x] Agregar un campo nuevo a `GridState` requiere editar solo un archivo
- [x] `loadGridDefaults` ya no contiene el ternario de ramas idénticas
- [x] Ambas páginas Create siguen creando grid executors con comportamiento idéntico

## Notas
Habilita [[ARCH-033]]: al mover `GridState`/`GridAction` a `lib/`, `GridConfigPanel` deja de
importar tipos desde `@/pages/CreateGridExecutor`.

### Implementación
- State machine extraída a `frontend/src/lib/gridExecutor.ts`: `GridState`, `GridAction`,
  `GRID_DEFAULTS`, `GRID_PERSISTED_FIELDS`, `GRID_STORAGE_KEY`, `LAST_MARKET_KEY`,
  `loadGridDefaults`, `saveGridDefaults`, `gridReducer`, `isSpotConnector`, `INTERVALS`,
  `LOOKBACK_OPTIONS`. Ambas páginas Create importan de ahí.
- **Divergencia parametrizada (no alineada en silencio):** las dos copias del `load` NO eran
  idénticas. `CreateExecutor.loadGridDefaults` además sobreescribe `connector`/`pair` desde el
  último mercado usado (`condor_last_market`); `CreateGridExecutor.loadSavedDefaults` no lo hace.
  Se unificó en `loadGridDefaults(applyLastMarket = false)`: `CreateExecutor` llama con `true`,
  `CreateGridExecutor` con el default `false`. Comportamiento visible de cada página sin cambios.
- Ternario muerto (`raw ? {...GRID_DEFAULTS} : {...GRID_DEFAULTS}`) eliminado.
- Como efecto necesario del movimiento de tipos, `GridConfigPanel` ahora importa `GridState`/
  `GridAction` desde `@/lib/gridExecutor` en vez de `@/pages/CreateGridExecutor` (esto cierra de
  paso lo que anticipaba la nota de ARCH-033 para `GridConfigPanel`).
- Fuera de alcance (no tocado): `frontend/src/hooks/usePrefetchData.ts` redeclara
  `GRID_STORAGE_KEY` pero solo lee llaves crudas (no usa `GridState`); se deja como está.
- `tsc -b` exit 0. `eslint` sobre los 4 archivos: el módulo nuevo limpio; los 7 errores en
  `CreateExecutor.tsx` son `react-hooks/preserve-manual-memoization`/`exhaustive-deps` pre-existentes
  en bloques `useMemo`/`useEffect` no modificados (líneas desplazadas por el borrado, no nuevos).

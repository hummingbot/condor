---
id: ARCH-014
title: BacktestingTab() (~601 líneas) mezcla todo el data-fetching, mutaciones y estado de formulario con el render
category: architecture
impact: medium
effort: L
risk: medium
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:475-1076
  - frontend/src/pages/tabs/BacktestingTab.tsx:497-531
  - frontend/src/pages/tabs/BacktestingTab.tsx:550-574
  - frontend/src/hooks/useAgentExecutors.ts
  - frontend/src/hooks/useMainControllerData.ts
commits:
  - 8b30623
created: 2026-06-10
---

## Problema
El componente `BacktestingTab` (`BacktestingTab.tsx:475-1076`, ~601 líneas) concentra: 11
`useState` de formulario/UI (480-494), 4 `useQuery` (configs, task list con refetch 5s, selected
task con refetch condicional 2s en pending/running, pinned task) en 497-531, 2 `useMutation`
(submit/delete) en 550-574 con `invalidateQueries(['backtest-tasks', server])`, efectos de
auto-selección y toast (534-547), y todo el JSX. El data layer de backtest (queries + mutaciones +
invalidaciones por server) no es reutilizable y está acoplado al render; cualquier cambio en cómo
se piden/cachean tasks obliga a navegar un archivo gigantesco.

## Solución propuesta
Extraer un hook `useBacktest(server)` que encapsule las 4 queries, las 2 mutaciones, los queryKeys
`['backtest-tasks']`/`['backtest-task']` y la lógica de invalidación, devolviendo
`{ tasks, selectedTask, pinnedTask, submit, remove, ... }`. El componente queda con estado de
formulario + render. Coherente con la convención del repo (`hooks/useMainControllerData`,
`hooks/useAgentExecutors` ya hacen esto para otras vistas).

## Criterio de aceptación
- [x] Existe un hook `hooks/useBacktest.ts` que contiene las queries y mutaciones de backtest
- [x] `BacktestingTab()` ya no declara `useQuery`/`useMutation` inline para tasks; los consume del hook
- [x] El `refetchInterval` de la task seleccionada (2s en pending/running) se preserva dentro del hook
- [x] Enviar, listar, seleccionar, fijar y borrar backtests funciona igual que antes

## Notas
Effort L. Conviene hacerlo después de [[ARCH-012]] (extraer `extractResults`) y [[PERF-001]]
(memoización) para reducir la superficie del archivo antes del refactor estructural.

### Cierre

Extraído a `frontend/src/hooks/useBacktest.ts`:
- Las 4 `useQuery` (`available-configs`, `backtest-tasks` con refetch 5s, `backtest-task`
  seleccionada con refetch condicional 2s en pending/running, `backtest-task` fijada).
- Las 2 `useMutation` (submit / delete) con sus query keys y la invalidación
  `['backtest-tasks', server]` por server.
- El estado `selectedTaskId` / `pinnedTaskId` (de los que dependen las queries y que mutan
  las mutaciones) y el efecto de auto-selección de la primera task completada.
- El hook expone `{ configsData, tasks, tasksLoading, selectedTask, selectedTaskLoading,
  selectedTaskId, setSelectedTaskId, pinnedTask, pinnedTaskId, setPinnedTaskId, submit, remove }`.

Dejado en `BacktestingTab` (capa de presentación / form):
- Estado de formulario y UI (configId, resolution, tradeCost, fechas, dropdown, search,
  preset, toast) y el efecto de auto-dismiss del toast.
- `submitBacktest()`: arma el payload desde el form y llama `submit.mutate(payload, …)`,
  pasando el toast como `onSuccess` por-llamada (el `onSuccess` del hook hace la
  invalidación + selección de la nueva task). El toast y el form quedan fuera del hook a
  propósito para mantenerlo libre de presentación.

Fuera de alcance (no tocado, sin scope creep):
- El efecto del chart (`BacktestChart`) y su guard de cancelación (CORR-007), intactos.
- El componente presentacional `BacktestResults` y el JSX más allá de consumir el hook.
- La regla `react-hooks/set-state-in-effect` sigue marcando el efecto de auto-selección:
  se movió verbatim desde el componente (era parte del baseline de 96 errores), por lo que
  el conteo neto del codebase no cambia (96 errores / 23 warnings antes y después). No se
  suprimió para no divergir de las otras 22 ocurrencias del repo.

Verificación: `npx tsc -b` exit 0; `npx vite build` exit 0; eslint del codebase sin cambios
en el conteo (96 errores / 23 warnings).

---
id: ARCH-014
title: BacktestingTab() (~601 líneas) mezcla todo el data-fetching, mutaciones y estado de formulario con el render
category: architecture
impact: medium
effort: L
risk: medium
status: todo
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:475-1076
  - frontend/src/pages/tabs/BacktestingTab.tsx:497-531
  - frontend/src/pages/tabs/BacktestingTab.tsx:550-574
  - frontend/src/hooks/useAgentExecutors.ts
  - frontend/src/hooks/useMainControllerData.ts
commits: []
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
- [ ] Existe un hook `hooks/useBacktest.ts` que contiene las queries y mutaciones de backtest
- [ ] `BacktestingTab()` ya no declara `useQuery`/`useMutation` inline para tasks; los consume del hook
- [ ] El `refetchInterval` de la task seleccionada (2s en pending/running) se preserva dentro del hook
- [ ] Enviar, listar, seleccionar, fijar y borrar backtests funciona igual que antes

## Notas
Effort L. Conviene hacerlo después de [[ARCH-012]] (extraer `extractResults`) y [[PERF-001]]
(memoización) para reducir la superficie del archivo antes del refactor estructural.

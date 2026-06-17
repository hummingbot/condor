---
id: PERF-042
title: Los paneles de config de executor recomputan la validación una segunda vez, duplicando el trabajo del hook de config
category: performance
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/components/executor/OrderConfigPanel.tsx:110
  - frontend/src/components/executor/OrderConfigPanel.tsx:184
  - frontend/src/components/executor/PositionConfigPanel.tsx:133
  - frontend/src/components/executor/PositionConfigPanel.tsx:226
  - frontend/src/components/executor/DCAConfigPanel.tsx:197
  - frontend/src/components/executor/DCAConfigPanel.tsx:330
commits: [19e3bec]
created: 2026-06-10
---

## Problema
Cada hook de config ya memoiza la validación desde `state`: `useOrderConfig` llama
`useOrderValidation(state)` (`OrderConfigPanel.tsx:110`), `usePositionConfig` llama
`usePositionValidation(state)` (`PositionConfigPanel.tsx:133`) y `useDCAConfig` llama
`useDCAValidation(state)` (`DCAConfigPanel.tsx:197`). Los paneles presentacionales correspondientes
vuelven a llamar exactamente el mismo hook de validación sobre el mismo `state`:
`OrderConfigPanel.tsx:184`, `PositionConfigPanel.tsx:226`, `DCAConfigPanel.tsx:330`. `CreateExecutor.tsx`
monta tanto el hook (sostiene `positionConfig.validation`/`orderConfig.validation`/`dcaConfig.validation`
en 359-363) como el panel (`<DCAConfigPanel state={dcaConfig.state} .../>` en 683), así que el validador
**corre dos veces por keystroke** sobre el mismo state. El validador de DCA en particular itera los
arrays de precios/amounts con chequeos de orden O(n) (170-188), y `DCAConfigPanel` además recomputa
`bep` (335-343) duplicando el cálculo del hook (217-229). Cada `useMemo` tiene su propia cache → el
segundo es recompute puro desperdiciado, y la validación del panel puede divergir momentáneamente de la
del hook que usa el padre.

## Solución propuesta
Pasar la `validation` ya computada (y donde se use, `bep`/valores derivados) desde el hook de config al
panel como prop, en vez de re-correr `use*Validation(state)` dentro del panel. Los padres ya tienen el
resultado del hook, así que pueden forwardear `dcaConfig.validation` etc. Eliminar la línea redundante
`const validation = use*Validation(state)` en cada panel. Esto quita una pasada completa de validación
por render y garantiza que padre y panel muestren los mismos errores.

## Criterio de aceptación
- [x] Cada panel renderiza errores de validación idénticos a antes
- [x] `use*Validation(state)` se invoca una vez por cambio de state, no dos (verificable con un counter/log en dev)
- [x] Sin errores de tipos por el prop-drilling; `ValidationMessages` sigue recibiendo el mismo shape de errors/warnings

## Notas
Estos paneles se usan solo en `CreateExecutor.tsx` (`CreateGridExecutor` usa `GridConfigPanel`, fuera de
scope). Impacto bajo pero elimina recompute O(n) por keystroke en DCA.

### Implementación
Se agregó `validation: ExecutorValidation` como prop a `OrderConfigPanel`, `PositionConfigPanel` y
`DCAConfigPanel`, eliminando la llamada interna `use*Validation(state)`. `CreateExecutor.tsx` ya sostenía
el resultado del hook (`*Config.validation`) y ahora lo forwardea al panel. Comportamiento de validación
y UI sin cambios.

Fuera de scope (no tocado): el recompute de `bep` en `DCAConfigPanel` (335-343) sigue local porque
`useDCAConfig` calcula el BEP dentro del `useMemo` de `chartProps` pero no lo expone en su return;
exponerlo requeriría cambiar el shape del hook y refactorizar `chartProps`, lo cual excede el cambio
mínimo de dedup de validación. Tampoco se tocaron los exports de los hooks `use*Validation` (siguen
exportados; los warnings react-refresh/only-export-components son baseline preexistente).

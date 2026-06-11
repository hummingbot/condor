---
id: CORR-028
title: ActiveBotsTab usa controller_name como React key de fila mientras dedup/selección usan controller_id
category: correctness
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/pages/tabs/ActiveBotsTab.tsx:764
  - frontend/src/pages/tabs/ActiveBotsTab.tsx:593
  - frontend/src/pages/tabs/ActiveBotsTab.tsx:761-768
commits: []
created: 2026-06-10
---

## Problema
Los controllers se deduplican por `${ctrl.bot_name}:${ctrl.controller_id || ctrl.controller_name}`
(`ActiveBotsTab.tsx:593`), y la identidad/selección/sparkline en todos lados usa
`controller_id || controller_name` (cid en 761, `selectedKey` en 767-768, `sparklineMap[cid]` en
771). Pero la React key de la fila renderizada es `${ctrl.bot_name}-${ctrl.controller_name}`
(`ActiveBotsTab.tsx:764`), que omite `controller_id`. `controller_id` y `controller_name` son
campos string distintos (`lib/api.ts:80-81`). Si un bot corre dos controllers con el mismo
`controller_name` pero `controller_id` distinto, el dedup mantiene ambas filas (keys de map
distintas) pero sus React keys colisionan → bugs de reconciliación: fila equivocada resaltada como
seleccionada, y estado de componente/sparkline adjunto al controller equivocado.

## Solución propuesta
Hacer la key de fila consistente con la identidad de dedup/selección:
`key={`${ctrl.bot_name}-${ctrl.controller_id || ctrl.controller_name}`}`, matcheando el `cid`
usado para `selectedKey` y los lookups de `sparklineMap`.

## Criterio de aceptación
- [ ] La key de `ControllerRow` usa `controller_id || controller_name`, igual que el map de dedup y `selectedKey`
- [ ] Dos controllers con el mismo `controller_name` en un bot renderizan como filas distintas con selección y sparkline correctas por fila
- [ ] No hay warning de key duplicada para controllers homónimos

## Notas
Fix de una línea, bajo riesgo.

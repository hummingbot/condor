---
id: ARCH-033
title: Componentes y tipos leaf importados directo desde módulos de página (acoplamiento page-to-page)
category: architecture
impact: medium
effort: M
risk: low
status: todo
files:
  - frontend/src/components/agent/AgentSessionContent.tsx:15
  - frontend/src/components/grid/GridConfigPanel.tsx:12
  - frontend/src/pages/Executors.tsx:280
  - frontend/src/pages/Executors.tsx:445
commits: []
created: 2026-06-10
---

## Problema
`AgentSessionContent.tsx:15` importa `DetailPanel`, `ExecutorTable`, `SortDir` y `SortKey` desde
`@/pages/Executors`, y `GridConfigPanel.tsx:12` importa los tipos `GridState`/`GridAction` desde
`@/pages/CreateGridExecutor`. Que componentes importen desde módulos de página invierte la dirección
de dependencia: un componente leaf/compartido ahora arrastra una route page entera (`Executors.tsx`
son ~1458 líneas con su propio data-fetching, suscripciones WS y tabla de 2000 filas), inflando el
chunk del bundle para cualquier consumidor y creando acoplamiento entre rutas. También hace imposible
lazy-loadear la página independientemente del componente. (`DetailPanel`/`ExecutorTable` son imports
de valor en runtime; `GridState`/`GridAction` son `import type`, bundle-neutral pero igual de
arquitectura invertida.)

## Solución propuesta
Mover las piezas genuinamente compartidas fuera de los archivos de página: relocalizar `DetailPanel`
y `ExecutorTable` (más `SortKey`/`SortDir`) a `frontend/src/components/executor/` (ej.
`ExecutorTable.tsx` / `ExecutorDetailPanel.tsx`), y mover `GridState`/`GridAction` (con el resto de la
state machine de [[ARCH-031]]) a `lib/gridExecutor.ts`. Que `Executors.tsx` y las páginas Create
importen desde esos módulos compartidos en vez de al revés.

## Criterio de aceptación
- [ ] Ningún archivo bajo `components/` importa desde `@/pages/*`
- [ ] `ExecutorTable`/`DetailPanel` y `GridState`/`GridAction` viven en módulos `components/` o `lib/`
- [ ] La página Executors y la vista de sesión del Agent renderizan igual usando los componentes relocalizados

## Notas
La mitad de `GridState`/`GridAction` se resuelve junto con [[ARCH-031]]. `components/executor/` ya existe.

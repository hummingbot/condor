---
id: ARCH-033
title: Componentes y tipos leaf importados directo desde módulos de página (acoplamiento page-to-page)
category: architecture
impact: medium
effort: M
risk: low
status: done
files:
  - frontend/src/components/agent/AgentSessionContent.tsx:15
  - frontend/src/components/grid/GridConfigPanel.tsx:12
  - frontend/src/pages/Executors.tsx:280
  - frontend/src/pages/Executors.tsx:445
commits: [72bf0db]
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
- [x] Ningún archivo bajo `components/` importa desde `@/pages/*`
- [x] `ExecutorTable`/`DetailPanel` y `GridState`/`GridAction` viven en módulos `components/` o `lib/`
- [x] La página Executors y la vista de sesión del Agent renderizan igual usando los componentes relocalizados

## Notas
La mitad de `GridState`/`GridAction` se resuelve junto con [[ARCH-031]]. `components/executor/` ya existe.

### Resuelto (cierre)
- La parte de `GridState`/`GridAction` **ya estaba hecha por [[ARCH-031]]**: viven en `lib/gridExecutor.ts`
  (`GridState` línea 3, `GridAction` línea 29) y `components/grid/GridConfigPanel.tsx:12` ya importa
  `import type { GridState, GridAction } from "@/lib/gridExecutor"`. No se tocó.
- Parte pendiente de este item (lo que sí se hizo): se movieron `ExecutorTable`, `DetailPanel` y los
  tipos `SortKey`/`SortDir` (más los helpers de soporte `compareExecutors`, `StatusDot`, `SortHeader`)
  desde `@/pages/Executors` a un nuevo módulo compartido
  `frontend/src/components/executor/ExecutorTable.tsx`. Movimiento mecánico (mismo componente, nuevo
  hogar), sin cambios de comportamiento renderizado (se preservaron literalmente los escapes `—`/`…`).
- Se actualizaron los importadores: `pages/Executors.tsx` y `components/agent/AgentSessionContent.tsx`
  ahora importan desde el módulo compartido en vez de page-to-page. Se limpiaron imports que quedaron
  sin uso en `Executors.tsx` (iconos lucide y formatters que solo usaban los bloques movidos).
- Verificación: `npx tsc -b` sale 0; `npx eslint src` sigue en 96 errores / 23 warnings (baseline sin
  cambios — el único error `no-empty` en el módulo nuevo es el `catch {}` original que se movió tal cual).

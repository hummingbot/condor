---
id: PERF-002
title: Memoizar/virtualizar la tabla de Executors — hasta 2000 filas se re-renderizan completas en cada poll
category: performance
impact: high
effort: M
risk: medium
status: todo
files:
  - frontend/src/pages/Executors.tsx:993-994
  - frontend/src/pages/Executors.tsx:351-435
  - frontend/src/pages/Executors.tsx:313-316
  - frontend/src/pages/Executors.tsx:1027
commits: []
created: 2026-06-10
---

## Problema
La página carga hasta `PAGE_SIZE(500) * maxPages(4) = 2000` executors
(`Executors.tsx:993-994`) y los renderiza en un `<table>` plano vía `sorted.map()` con
cada fila construida inline (351-435). No hay memoización de fila ni virtualización.
`ExecutorTable` no está memoizado y se renderiza dos veces (lista activa y History
expandible, default-expanded). La lista re-ordena (313-316) y re-renderiza todas las
filas en cada refetch del infinite query (`refetchInterval: 10000`, línea 1027). Con
miles de filas en el DOM, cada update produce una reconciliación + layout grande →
jank/stutter visible al scrollear.

> Nota del verificador: el mensaje WS de executors **no** dispara este re-render (escribe
> en una key de cache que no matchea — ver [[CORR-006]]); el driver real es el poll de 10s.

## Solución propuesta
Dos fixes complementarios: (1) extraer la fila a un componente memoizado
`const ExecutorRow = React.memo(function ExecutorRow({ ex, ... }) {...})` pasándole props
primitivas/estables (`isSelected`, `isChecked`, callbacks de formato ya estables) para que
las filas sin cambios salten el re-render. (2) Virtualizar el `tbody` con
`@tanstack/react-virtual` (no está instalado aún — sería un `npm i`) para montar solo las
filas en pantalla. Si la virtualización es demasiado invasiva a corto plazo, como mínimo
aplicar la fila memoizada.

## Criterio de aceptación
- [ ] Scrollear con 1000+ filas es fluido (sin long-task >50ms en DevTools Performance)
- [ ] Un update de la lista no re-renderiza todas las filas — verificado con React DevTools Profiler mostrando solo filas cambiadas commiteando
- [ ] Ordenamiento, selección, click a detalle y export CSV siguen idénticos

## Notas
Relacionado con [[CORR-006]] (key mismatch del WS): conviene arreglar primero CORR-006 para
que el WS sí actualice la lista, y entonces la memoización de fila evita el costo del update.
`@tanstack/react-virtual` es dependencia nueva.

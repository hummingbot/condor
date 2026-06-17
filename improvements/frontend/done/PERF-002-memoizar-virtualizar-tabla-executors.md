---
id: PERF-002
title: Memoizar/virtualizar la tabla de Executors — hasta 2000 filas se re-renderizan completas en cada poll
category: performance
impact: high
effort: M
risk: medium
status: done
files:
  - frontend/src/components/executor/ExecutorTable.tsx
commits: [a119fce]
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
- [ ] Scrollear con 1000+ filas es fluido (sin long-task >50ms en DevTools Performance) — parcial: la memoización reduce el costo de reconciliación por update pero NO reduce el tamaño del DOM (siguen montadas hasta ~2000 filas). Requiere virtualización para garantizarse; ver Notas.
- [x] Un update de la lista no re-renderiza todas las filas — `ExecutorRow` ahora es `React.memo` con props primitivas/estables; React Query usa structural sharing por defecto, así que las filas sin cambios conservan su referencia y saltan el render.
- [x] Ordenamiento, selección, click a detalle y export CSV siguen idénticos — solo se extrajo la fila a un componente; sin cambios de comportamiento.

## Notas

### Refs desactualizados (post ARCH-033)
Todos los `files:` del frontmatter original apuntaban a `frontend/src/pages/Executors.tsx`
(líneas 993-994, 351-435, 313-316, 1027), pero ARCH-033 movió `ExecutorTable`/`DetailPanel`
fuera de la página a `frontend/src/components/executor/ExecutorTable.tsx`. La fila inline vivía
ahí (`sorted.map`), no en `Executors.tsx`. Se trabajó sobre la ubicación real.

### Qué se hizo
- Se extrajo la fila a `const ExecutorRow = memo(function ExecutorRow(...))` con props
  primitivas (`isSelected`, `isChecked`, `isStopping`) y callbacks estables
  (`onRowClick` = `setSelectedExecutor`, `onToggleSelect`/`onStop` ya con `useCallback` en
  `Executors.tsx`). La comparación shallow por defecto de `React.memo` es correcta porque
  todas las props son primitivas o referencialmente estables.
- Se estabilizaron los formatters de fallback (`fmtPnl`/`fmtVol`/`fmtDet`) con `useMemo`
  para no crear closures nuevas en cada render (los `rateFormat*` ya vienen memoizados de
  `useRates`). Sin esto la memoización de fila se rompería cuando no hay rate formatter.

### Qué se saltó y por qué
- **Virtualización (`@tanstack/react-virtual`)**: NO implementada. El item la marca como
  dependencia nueva y la prioridad era la optimización de bajo riesgo sin nuevas
  dependencias. Virtualizar un `<table>` semántico es invasivo (requiere
  `position: absolute`/`transform` en filas o cambiar a layout no-`<table>`, romper sticky
  header, manejar resize) y de riesgo medio-alto; queda como follow-up si tras medir con
  Profiler el tamaño del DOM sigue causando jank. El criterio de scroll fluido queda
  parcial por esto.
- **React Compiler**: este repo NO lo tiene habilitado (verificado en `vite.config.ts` —
  solo `@vitejs/plugin-react`, sin `babel-plugin-react-compiler`), así que la memoización
  manual es necesaria y no entra en conflicto con el compilador.

### Verificación
- `npx tsc -b` → exit 0.
- `npx eslint` sobre el archivo: 1 error `no-empty` preexistente (en el `catch {}` de
  `DetailPanel`, antes línea 654, ahora 702 por el código agregado). Cero errores nuevos.

Relacionado con [[CORR-006]] (key mismatch del WS): conviene arreglar primero CORR-006 para
que el WS sí actualice la lista, y entonces la memoización de fila evita el costo del update.

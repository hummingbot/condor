---
id: CORR-006
title: Los updates WS nunca llegan al infinite-query de executors (key mismatch) — la lista solo refresca con el poll de 10s
category: correctness
impact: high
effort: S
risk: low
status: done
files:
  - frontend/src/pages/Executors.tsx:1018
  - frontend/src/hooks/useWebSocket.ts:123
  - frontend/src/pages/Executors.tsx:1027
commits:
  - "da2f549 (fix) alinear queryKey del WS con executors-infinite (CORR-006)"
created: 2026-06-10
---

## Problema
`Executors.tsx` crea el query de la lista con `queryKey: ["executors-infinite", server]`
(2 elementos, `Executors.tsx:1018`). El handler WS intenta empujar updates en vivo al cache
infinito con `queryClient.setQueryData(["executors-infinite", server, ""], ...)` (3 elementos,
`useWebSocket.ts:122-134`, key en línea 123). React Query matchea keys de forma estructural
exacta, así que un array de 3 elementos hashea a una entrada de cache distinta de la de 2
elementos: el write WS cae en una entrada que ningún componente lee. La lista de executors
**no se actualiza en vivo desde el canal WS `executors`** como se pretendía — solo refresca
vía `refetchInterval: 10000` (`Executors.tsx:1027`), quedando hasta 10s stale, y la plomería
WS es código muerto. (El `invalidateQueries` en `Executors.tsx:1054` usa la key de 2 elementos
y funciona porque invalidate es por prefijo, lo que enmascara el bug.)

## Solución propuesta
Alinear las keys. Cambiar el write WS en `useWebSocket.ts:123` a `["executors-infinite", server]`
para que matchee el query (más seguro que tocar el queryKey del componente). Verificar que la
lógica de patch de página (`useWebSocket.ts:124-133`, `slice(0, limit)` sobre `page[0]`) sigue
matcheando el objeto que devuelve `getExecutorsPage` (`api.ts:822` → `{ executors, next_cursor }`)
para que la primera página mergeada quede consistente con la paginación por cursor.

## Criterio de aceptación
- [ ] La key del `setQueryData` WS para la lista infinita es idéntica al `queryKey` del `useInfiniteQuery` en `Executors.tsx:1018`
- [ ] Con WS conectado, un cambio de status/PnL de un executor se refleja en la lista antes de que dispare el poll de 10s
- [ ] No hay regresión en los updates de `["executors", server, ""]` no filtrados (siguen funcionando)

## Notas
Pre-requisito de [[PERF-002]]: una vez que el WS sí actualiza la lista, la memoización de fila
de PERF-002 es lo que evita que el update re-renderice las 2000 filas.

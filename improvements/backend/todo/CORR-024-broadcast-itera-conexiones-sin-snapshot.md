---
id: CORR-024
title: broadcast() itera self._connections con await adentro sin snapshot
category: correctness
impact: low
effort: S
risk: low
status: todo
files:
  - condor/web/ws_manager.py:817
commits: []
created: 2026-06-10
---

## Problema
`broadcast()` itera `for conn in self._connections:` (`ws_manager.py:817`) y
dentro del loop hay un punto de await real: `await self._send(conn, ...)`
(línea 820). Mientras el broadcast está suspendido en ese await, otra corutina
puede ejecutar `disconnect(conn)` que muta la lista con
`self._connections.remove(conn)` (línea 308). Esto es alcanzable: cada conexión
WS corre su propia corutina en `routes/ws.py` y al desconectarse ejecuta
`finally: manager.disconnect(conn)` de forma síncrona. Mutar una lista durante
un `for` en Python (eliminar un elemento antes del cursor) hace que el loop
**salte el siguiente elemento** — esa conexión adyacente se pierde un tick de
broadcast (se recupera en el siguiente, de ahí el impacto bajo).

## Solución propuesta
Tomar un snapshot antes de iterar: `for conn in list(self._connections):`. Es
idiomático y ya se usa en este mismo archivo (línea 310:
`for channel in list(conn.channels):`). No rompe nada: la limpieza de muertas y
`disconnect()` siguen siendo idempotentes por el guard `if conn in
self._connections` (línea 307).

## Criterio de aceptación
- [ ] `broadcast()` itera sobre un snapshot (`list(self._connections)`) en vez de la lista viva
- [ ] No se saltean conexiones si una se remueve durante la iteración
- [ ] No se rompe ningún test existente

## Notas
Si se implementa [[PERF-020]] (gather), este item se subsume parcialmente porque
el snapshot se construye igual al armar la lista de tasks — coordinar. Impacto
real acotado: no es "pérdida de datos iniciales", es saltarse un único tick de
broadcast para la conexión adyacente a la removida.

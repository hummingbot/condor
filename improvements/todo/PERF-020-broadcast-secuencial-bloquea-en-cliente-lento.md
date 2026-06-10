---
id: PERF-020
title: Broadcast secuencial bloquea a todos los clientes ante uno lento (usar gather)
category: performance
impact: high
effort: M
risk: medium
status: todo
files:
  - condor/web/ws_manager.py:814
commits: []
created: 2026-06-10
---

## Problema
`broadcast()` (`ws_manager.py:814-826`) envía a cada cliente suscripto en serie
con `await self._send(conn, channel, data)` dentro del `for` (línea 820). Si un
cliente tiene la red lenta o backpressure, el `await` de ese envío bloquea a
**todos** los clientes siguientes en el mismo tick de broadcast — un fan-out
O(N) secuencial por tick. Esto se dispara en cada actualización vía
`_broadcast_update -> broadcast` (líneas 808-810), que ocurre con alta
frecuencia en canales como `bots_ws`, `positions_ws` y `candles`.

## Solución propuesta
Hacer fan-out concurrente de los envíos con
`asyncio.gather(*tasks, return_exceptions=True)`: construir un task por cada
conexión suscripta, esperarlas todas juntas, y después del gather recolectar las
que devolvieron excepción para desconectarlas en batch. El envío es seguro de
paralelizar: cada `_send` toca solo su propio `conn.ws` (línea 827), no hay lock
ni mutación compartida dentro del loop (`_last_data[channel]` se setea una vez
antes, línea 815), `disconnect()` es idempotente (guard en línea 307), y el
orden por socket se preserva porque cada conexión recibe exactamente un envío
por tick.

## Criterio de aceptación
- [ ] `broadcast()` usa `asyncio.gather(..., return_exceptions=True)` en vez de awaits secuenciales
- [ ] Todos los envíos de un mismo broadcast se disparan concurrentemente
- [ ] Las conexiones muertas se siguen limpiando correctamente tras el gather
- [ ] No se rompe ningún test existente

## Notas
Combina bien con [[CORR-022]] (loguear el fallo por cliente) y [[CORR-024]]
(snapshot de conexiones): los tres tocan `broadcast()`. Si se implementan juntos,
coordinar para no pisarse. Mantener el logueo del fallo por cliente al procesar
las excepciones del gather.

---
id: PERF-004
title: Eviction O(n) con min() en _CandleBuffer._evict (O(n²) en backfill)
category: performance
impact: high
effort: M
risk: low
status: done
files:
  - condor/web/ws_manager.py:85
  - condor/web/ws_manager.py:524
  - condor/web/ws_manager.py:498
commits:
  - "0f02491 (perf) batch eviction + helper de subscribers + log de except silencioso en ws_manager (PERF-004, READ-015, READ-017)"
created: 2026-06-10
---

## Problema
`_CandleBuffer._evict()` (líneas 85-88 de `condor/web/ws_manager.py`) ejecuta
`while len(self._data) > max_size: del self._data[min(self._data)]`, llamando `min(self._data)` —que
es O(n)— en cada iteración. Esto ocurre en dos rutas calientes:
1. **Cada `upsert`/`upsert_many`** del stream de candles (líneas 73-79), 1-5s por tick.
2. **Backfill** vía `upsert_many()` (línea 524): al insertar miles de candles de golpe se evictan
   muchos a la vez, dando O(n²). Sin límite en `get_historical_candles()` (línea 498), un buffer de
   200 con 5000+ candles entrantes ejecuta millones de comparaciones (~61ms vs ~0.3ms con batch).

## Solución propuesta
Sustituir el `min()` repetido por una eviction en batch que aproveche el orden por timestamp:
calcular `excess = len - max_size` y eliminar los `excess` timestamps más antiguos de una sola pasada
(p. ej. manteniendo `self._data` como `collections.OrderedDict`/`sortedcontainers.SortedDict` ordenado
por clave, o recortando con `sorted(self._data)[:excess]` una sola vez por llamada en lugar de por
candle). Mantener el acceso por clave (no usar un `deque` plano, que perdería el acceso por timestamp).

## Criterio de aceptación
- [x] La eviction respeta `max_size` con la misma semántica (descarta los candles más antiguos)
- [x] La eviction es O(n log n) por llamada como máximo, no O(n) por candle eliminado
- [x] El acceso por timestamp (`__getitem__`/`in`) sigue disponible
- [x] El backfill de miles de candles ya no escala cuadráticamente
- [x] No se rompe ningún test existente

## Notas
Fusiona dos hallazgos del análisis (per-upsert O(n) y backfill O(n²)) porque comparten exactamente la
misma causa raíz: el `min(self._data)` dentro del loop de `_evict`. Evaluar también acotar
`get_historical_candles()` (línea 498) a `max_size`.

**Cierre (0f02491):** Resuelto: `_evict()` ahora calcula `excess` y borra los más antiguos en una sola pasada con `sorted(self._data)[:excess]`. No se acotó `get_historical_candles()` (era opcional, no criterio).

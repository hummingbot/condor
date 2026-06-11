---
id: READ-017
title: except Exception: pass sin logging en _bots_ws_stream rompe observabilidad
category: readability
impact: low
effort: S
risk: low
status: done
files:
  - condor/web/ws_manager.py:1438
commits:
  - "0f02491 (perf) batch eviction + helper de subscribers + log de except silencioso en ws_manager (PERF-004, READ-015, READ-017)"
created: 2026-06-10
---

## Problema
En `_bots_ws_stream()` de `condor/web/ws_manager.py` (líneas 1438-1439) hay un `except Exception: pass`
sin logging, en plena fase de inicialización del stream. El **mismo método** maneja el mismo tipo de
error con `logger.debug(...)` en las líneas 1472-1473, y `_on_data_update()` (línea 770) también loguea
transformaciones fallidas. El silencio en la inicialización hace imposible diagnosticar por qué el
dashboard aparece vacío.

## Solución propuesta
Añadir un `logger.debug(...)` en el `except` de las líneas 1438-1439, replicando exactamente el patrón
ya usado en la línea 1473 del mismo método (mensaje describiendo el fallo de inicialización del stream).

## Criterio de aceptación
- [x] El `except` de las líneas 1438-1439 loguea el error (no es `pass` silencioso)
- [x] El patrón de logging es consistente con el de la línea 1473
- [x] No cambia el flujo de control (sigue sin propagar la excepción)
- [x] No se rompe ningún test existente

## Notas
Mejora de observabilidad/consistencia, no cosmética: hoy un fallo en la inicialización del stream es
invisible. Relacionado con el módulo de [[READ-015]].

**Cierre (0f02491):** Resuelto: `logger.debug(...)` agregado al except de inicialización, consistente con el patrón ya presente en el mismo método.

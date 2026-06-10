---
id: READ-015
title: Lógica de check de subscribers duplicada en los métodos _maybe_stop_*_stream
category: readability
impact: medium
effort: M
risk: low
status: done
files:
  - condor/web/ws_manager.py:1407
commits:
  - "0f02491 (perf) batch eviction + helper de subscribers + log de except silencioso en ws_manager (PERF-004, READ-015, READ-017)"
created: 2026-06-10
---

## Problema
Los métodos `_maybe_stop_*_stream()` de `condor/web/ws_manager.py` (al menos 7, incluido
`_maybe_stop_bots_ws_stream` en línea 1407) repiten el mismo patrón:
`for conn in self._connections: if channel in conn.channels: return`. Hay 16+ instancias del patrón de
verificación de suscriptores en total. La duplicación dificulta el mantenimiento y hace fácil
introducir inconsistencias al tocar uno solo.

## Solución propuesta
Extraer un helper, p. ej. `_has_subscribers(channel: str) -> bool`, que use
`any(channel in c.channels for c in self._connections)`, y reemplazar las instancias del patrón en los
métodos `_maybe_stop_*_stream`. Asegurar la firma correcta del parámetro (`channel: str`).

## Criterio de aceptación
- [x] Existe un único helper `_has_subscribers(channel)` con `any(...)`
- [x] Los 7 métodos `_maybe_stop_*_stream` (incl. el de la línea 1407) lo usan
- [x] El comportamiento de parada de streams no cambia
- [x] No se rompe ningún test existente

## Notas
El hallazgo original omitía la instancia de la línea 1407 y proponía un parámetro mal tipado; este item
ya lo corrige. La duplicación es más amplia (16+ usos), revisar si conviene extender el helper a todas.

**Cierre (0f02491):** Resuelto: helper `_has_subscribers(channel)` con `any(...)`, aplicado a los 7 métodos `_maybe_stop_*_stream`.

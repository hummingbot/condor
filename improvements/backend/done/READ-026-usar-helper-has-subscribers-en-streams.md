---
id: READ-026
title: Usar el helper _has_subscribers() en vez del check inline duplicado
category: readability
impact: low
effort: S
risk: low
status: done
files:
  - condor/web/ws_manager.py:1180
  - condor/web/ws_manager.py:1330
  - condor/web/ws_manager.py:1533
commits:
  - "cc6e508 (refactor) usar helper _has_subscribers en checks inline de las corutinas de stream (READ-026)"
created: 2026-06-10
---

## Problema
El helper `_has_subscribers(channel)` está definido en `ws_manager.py:837-839`
(`return any(channel in c.channels for c in self._connections)`) y ya se usa en 8
sitios (843, 1150, 1299, 1414, 1600, 1723, 1815, 1898). Sin embargo, las
corutinas de stream repiten el mismo check **inline**
`if not any(channel in c.channels for c in self._connections): return` en las
líneas 1180, 1330, 1533, 1654, 1750, 1842 — y también en 1919 (una ocurrencia
extra). Duplicación de lógica que ya tiene helper: inconsistente visualmente y
fácil de pasar por alto al refactorizar.

## Solución propuesta
Reemplazar cada check inline por `if not self._has_subscribers(channel): return`.
La expresión es idéntica, solo se delega al helper existente — comportamiento sin
cambios (mismo early-exit).

## Criterio de aceptación
- [x] Todas las corutinas de stream usan `_has_subscribers()` (las 7 ocurrencias del early-exit)
- [x] No quedan checks `any(channel in c.channels for c in self._connections)` inline fuera del propio helper
- [x] Comportamiento sin cambios (mismo early-exit)
- [x] No se rompe ningún test existente (no hay suite para ws_manager; verificado con AST parse + import + black/isort)

## Notas
Cambio mecánico y trivial. Si se hace junto con [[ARCH-025]] (dedup del
lifecycle), conviene hacer este primero o coordinarlos, ya que ambos tocan las
corutinas `_X_stream()`. El hallazgo original listaba 6 líneas pero hay 7
ocurrencias (faltaba la 1919).

Cerrado: además de los 7 early-exit `if not any(...)` de las corutinas de stream,
se delegaron al helper las otras dos ocurrencias inline (`_maybe_unsub_sds` y
`_deferred_stop_candle_stream`) para cumplir el criterio "no quedan checks inline
fuera del helper". Tras el cambio solo el cuerpo de `_has_subscribers` retiene la
expresión `any(...)`.

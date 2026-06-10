---
id: ARCH-025
title: Centralizar el lifecycle duplicado de _ensure_*_stream / _maybe_stop_*_stream
category: architecture
impact: medium
effort: M
risk: medium
status: todo
files:
  - condor/web/ws_manager.py:831
  - condor/web/ws_manager.py:1143
  - condor/web/ws_manager.py:1287
  - condor/web/ws_manager.py:1405
commits: []
created: 2026-06-10
---

## Problema
`ws_manager.py` tiene 8 pares casi idénticos de `_ensure_*_stream()` /
`_maybe_stop_*_stream()` (candle, trade, order_book, executor, bots_ws,
positions_ws, performance_ws, controller_perf) en líneas 831, 1143, 1287, 1405,
1591, 1711, 1803, 1886 — cada uno seguido de su par `_maybe_stop_*`. Para 7 de
los 8 tipos el cuerpo es casi byte-idéntico: `_ensure_*` es el mismo
`if channel in self._X_tasks and not done(): return; create_task; log` y
`_maybe_stop_*` el mismo `if self._has_subscribers: return; pop; if not done:
cancel; log`. Duplicación que infla el archivo y multiplica el riesgo de
divergencia al mantener/testear.

## Solución propuesta
Extraer el lifecycle a un registro genérico: un dict que mapee
`stream_type -> (task_dict, coroutine_factory, teardown_hook_opcional)`, con un
`_ensure_stream(stream_type, channel)` y un `_maybe_stop_stream(stream_type,
channel)` únicos que reemplacen los 8 pares. Las corutinas `_X_stream()` en sí
(con su lógica distinta de subscribe/parse/SDS) NO se tocan — solo se
centralizan los helpers de arranque/parada. Alinea con la tendencia reciente de
dedup/centralización del repo (ARCH-011/012/014 ya cerrados).

## Criterio de aceptación
- [ ] Un único `_ensure_stream(stream_type, channel)` reemplaza los 8 `_ensure_*` (salvo special-case de candle)
- [ ] Un único `_maybe_stop_stream(stream_type, channel)` reemplaza los 8 `_maybe_stop_*`
- [ ] Registro `stream_type -> {task_dict, coroutine_factory, teardown_hook}` documentado
- [ ] Comportamiento de start/stop de cada stream preservado (sin cambio funcional)
- [ ] No se rompe ningún test existente (añadir test del lifecycle genérico por tipo si aplica)

## Notas
**Excepción importante:** el par de candle NO es uniforme.
`_maybe_stop_candle_stream` (líneas 841-865) implementa un teardown diferido con
keep-alive (`_candle_teardown_timers`, `_CANDLE_KEEP_ALIVE`,
`_deferred_stop_candle_stream`) más tasks de poll-fallback. El factory debe
soportarlo vía un `teardown_hook` opcional, no asumir 8 tipos idénticos. El
"~500 líneas de duplicación" del hallazgo original estaba inflado: lo realmente
duplicado son ~110-130 líneas de los helpers de lifecycle; el resto del span son
las corutinas `_X_stream()` con lógica genuinamente distinta que NO se deduplica.
Acotar el scope a los helpers de lifecycle.

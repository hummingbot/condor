---
id: CORR-021
title: asyncio.create_task fire-and-forget sin guardar referencia (GC silencioso)
category: correctness
impact: medium
effort: S
risk: medium
status: done
files:
  - condor/web/ws_manager.py:456
  - condor/web/ws_manager.py:603
commits:
  - "8f5202e (fix) trackear tasks fire-and-forget de backfill/warm para evitar GC silencioso (CORR-021)"
created: 2026-06-10
---

## Problema
Dos `asyncio.create_task(...)` no guardan la referencia al task que crean,
rompiendo la convención del propio archivo (que trackea TODAS las tareas de
streaming en dicts dedicados, líneas 143-151, y las cancela en `stop()`,
líneas 224-269):
1. `ws_manager.py:456` — `asyncio.create_task(self._backfill_candles(channel))`
   dentro de `_handle_candle_subscribe`.
2. `ws_manager.py:603` — `asyncio.create_task(warm_portfolio_history(server_name))`
   dentro de `_subscribe_sds`.

Un task cuyo resultado no se referencia es un footgun documentado de Python: el
event loop solo mantiene una referencia débil, así que el GC puede recolectar y
**cancelar silenciosamente** el task antes de que termine. Eso compromete la
confiabilidad del backfill de candles y del warm de portfolio history.

## Solución propuesta
Guardar cada task en un dict del manager para mantener una referencia fuerte
(p.ej. `self._backfill_tasks: dict[str, asyncio.Task]` keyeado por canal), o como
mínimo mantener un `set` de tasks vivos y un callback `task.add_done_callback`
que lo remueva al completar. Replicar el patrón ya existente en el archivo y
cancelar los tasks trackeados en `stop()`. No hace falta lógica de cancelación
compleja: ambas tareas son finitas (`_backfill_candles` es one-shot;
`warm_portfolio_history` hace gather de 4 fetches y retorna) — el objetivo es
evitar el GC del fire-and-forget, no un leak de tareas long-lived.

## Criterio de aceptación
- [x] Ambos `create_task` guardan una referencia fuerte (dict/set del manager)
- [x] Las referencias se limpian al completar el task (o se cancelan en `stop()`)
- [x] No quedan tasks fire-and-forget sin referenciar en `ws_manager.py`
- [x] No se rompe ningún test existente (no hay suite para ws_manager; verificado con AST parse + import + black/isort)

## Notas
El `_refresh_loop` long-lived del lado portfolio (`portfolio.py:220`) YA está
trackeado en `_refresh_tasks` con auto-timeout y canceller dedicado, así que
NO entra en este item. El framing original de "memory leak crítico / tareas que
corren indefinidamente" estaba exagerado: el valor real es de
correctness/consistencia (evitar cancelación silenciosa por GC).

---
id: CORR-002
title: Dicts _stopping_bots/_stopping_controllers iterados y mutados sin sincronización
category: correctness
impact: high
effort: M
risk: medium
status: done
files:
  - condor/web/routes/bots.py:40
  - condor/web/routes/bots.py:64
  - condor/web/routes/bots.py:408
commits:
  - "bb5c92b (fix) sincronizar dicts _stopping_* con threading.Lock en bots routes (CORR-002)"
created: 2026-06-10
---

## Problema
`_stopping_bots` y `_stopping_controllers` (líneas 40-42 de `condor/web/routes/bots.py`) son dicts
globales sin lock. `get_stopping_bots()` (línea 64-72) y `get_stopping_controllers()` (línea 81-90)
**iteran** el dict mientras otros endpoints async lo **modifican** concurrentemente:
`mark_bot_stopping()` (`stop_bot_endpoint`, línea 821), `mark_controllers_stopping()`
(`stop_controllers_endpoint`, línea 849) y `clear_bot_stopping()` (líneas 393, 401, 408). El GIL no
protege aquí: las operaciones de iterar + modificar no son atómicas, y pueden producir
`RuntimeError: dictionary changed size during iteration` cuando `list_bots()` (línea 164) corre en
paralelo con un stop.

## Solución propuesta
Proteger todos los accesos a ambos dicts con un `threading.Lock` (no `asyncio.Lock`, porque las
funciones de lectura son síncronas). Adquirir el lock dentro de `get_stopping_bots`,
`get_stopping_controllers`, `mark_*_stopping` y `clear_bot_stopping`. Como mínimo, en las funciones de
lectura iterar sobre una copia (`list(dict.items())`) para no iterar la estructura viva.

## Criterio de aceptación
- [x] Todas las lecturas iteran sobre un snapshot o bajo lock
- [x] Todas las escrituras (`mark_*`, `clear_*`, `pop`) están protegidas por el mismo lock
- [x] `list_bots()` concurrente con `stop_bot`/`stop_controllers` no lanza `RuntimeError`
- [x] No se rompe ningún test existente

## Notas
Uvicorn corre múltiples coroutines en el mismo proceso, por lo que el escenario es realista al usar
los endpoints de stop mientras el dashboard refresca la lista de bots.

**Cierre (bb5c92b):** Resuelto: `threading.Lock` a nivel de módulo; lecturas sobre snapshot `list(...)` bajo lock; todas las escrituras/pop protegidas.

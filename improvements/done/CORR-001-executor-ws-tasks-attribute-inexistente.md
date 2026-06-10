---
id: CORR-001
title: Referencia a atributo inexistente _executor_ws_tasks provoca AttributeError en runtime
category: correctness
impact: high
effort: S
risk: low
status: done
files:
  - condor/web/ws_manager.py:758
  - condor/web/ws_manager.py:129
commits:
  - "b36b828 (fix) usar _executor_tasks en _on_data_update (CORR-001)"
created: 2026-06-10
---

## Problema
En `_on_data_update()` (líneas 758-759 de `condor/web/ws_manager.py`) el código referencia
`self._executor_ws_tasks`, pero ese atributo nunca se define. El `__init__` (línea 129) declara
`self._executor_tasks`. Cuando llega un mensaje de tipo `EXECUTORS`, el acceso a `_executor_ws_tasks`
lanza `AttributeError` y crashea el procesamiento de datos del WebSocket. El patrón correcto ya se usa
para `BOTS_STATUS` en las líneas 754-757 usando el nombre real del atributo.

## Solución propuesta
Renombrar `self._executor_ws_tasks` → `self._executor_tasks` en las líneas 758-759, alineándolo con la
definición del `__init__` (línea 129) y con el patrón usado para `BOTS_STATUS`. Verificar que no existan
otras referencias al nombre incorrecto (`grep _executor_ws_tasks`).

## Criterio de aceptación
- [x] No queda ninguna referencia a `_executor_ws_tasks` en el codebase
- [x] El flujo `EXECUTORS` en `_on_data_update()` se ejecuta sin `AttributeError`
- [x] No se rompe ningún test existente

## Notas
Es un bug latente: solo se dispara cuando llega data de tipo `EXECUTORS` por el WS. Fix trivial pero
de alto impacto (evita un crash). Riesgo bajo: es un rename de un nombre que hoy no resuelve a nada.

**Cierre:** Confirmado que el atributo correcto es `_executor_tasks` (definido en `__init__:142`, usado
en las líneas 224/227 y en el starter de streams de executors 1240-1251). El lado de bots usa un dict
dedicado `_bots_ws_tasks`, pero para executors no existe equivalente `_ws_` — `_executor_tasks` ES el
dict de tareas de stream. Se renombró la referencia en las (ahora) líneas 758-759. El repo no tiene
suite de tests; se validó por `grep` (sin referencias residuales) y parseo del módulo.

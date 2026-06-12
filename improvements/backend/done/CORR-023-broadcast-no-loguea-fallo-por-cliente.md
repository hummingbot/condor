---
id: CORR-023
title: broadcast() desconecta por excepción sin loguear la causa
category: correctness
impact: medium
effort: S
risk: low
status: done
files:
  - condor/web/ws_manager.py:819
commits:
  - "56a3e75 (fix) broadcast loguea fallo de envio por cliente antes de desconectar (CORR-023)"
created: 2026-06-10
---

## Problema
En `broadcast()` (`ws_manager.py:819-822`) el envío por cliente está envuelto en
`try: await self._send(...) except Exception: dead.append(conn)` — un
`except Exception:` sin `as e` y sin logging. Las conexiones marcadas como
muertas se pasan a `disconnect()` (líneas 306-309), que solo loguea
`"WS disconnected: user %s"` y nunca la excepción subyacente. Resultado: un drop
provocado por un fallo de envío (socket cerrado, error de serialización de
`send_json`, buffer) es **indistinguible** de una desconexión limpia del cliente,
y ni el canal ni la excepción quedan registrados. El operador no puede
diagnosticar por qué algunos clientes dejan de recibir actualizaciones.

## Solución propuesta
Cambiar `except Exception:` por `except Exception as e:` y loguear antes de
marcar la conexión muerta, incluyendo canal y `user_id`, p.ej.
`logger.warning("Broadcast send failed: channel=%s user=%s: %s", channel, conn.user_id, e)`.
Es zero-risk: no altera el control de flujo (la conexión se sigue agregando a
`dead` y se desconecta igual). Sigue el patrón del propio archivo (p.ej. líneas
556, 605, 802 ya loguean `"... %s", channel, e`).

## Criterio de aceptación
- [x] Cada fallo de envío en `broadcast()` se loguea con `channel`, `user_id` y la excepción
- [x] La limpieza de conexiones muertas (`disconnect`) sigue funcionando igual
- [x] No se rompe ningún test existente (no hay suite para ws_manager; verificado con AST parse + import + black/isort)

## Notas
Toca el mismo bloque que [[PERF-020]] (gather) y [[CORR-024]] (snapshot). Si se
implementa [[PERF-020]] primero, el logueo se hace al procesar las excepciones
devueltas por `gather(return_exceptions=True)`. Coordinar para no duplicar
esfuerzo. Mejora de observabilidad (no es un bug funcional), pero real y barata.

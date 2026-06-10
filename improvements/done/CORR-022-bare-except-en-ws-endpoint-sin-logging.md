---
id: CORR-022
title: bare except en el endpoint /ws traga errores sin loggear
category: correctness
impact: high
effort: S
risk: low
status: done
files:
  - condor/web/routes/ws.py:23
commits:
  - "cd8e4df (fix) loguear excepciones inesperadas en el endpoint /ws (CORR-022)"
created: 2026-06-10
---

## Problema
`websocket_endpoint()` (`condor/web/routes/ws.py:23-24`) tiene un
`except Exception:` seguido de `pass`, sin ningún logging. `WebSocketDisconnect`
ya se maneja por separado (línea 21-22), así que este handler genérico captura
solo errores **inesperados** y los descarta en silencio. `handle_message()`
(`ws_manager.py:354`) hace trabajo async sustancial (subscribe/backfill de
candles, polling REST, suscripción SDS, arranque de streams) que puede lanzar
excepciones — y todas desaparecen sin traza, imposibilitando diagnosticar fallos
en producción sobre un endpoint autenticado y sensible a seguridad.

## Solución propuesta
Reemplazar `except Exception: pass` por `except Exception: logger.exception(...)`
preservando la semántica de no-crashear (sin re-raise; el `finally:
manager.disconnect(conn)` sigue corriendo). Requiere agregar a `ws.py` un module
logger (`import logging; logger = logging.getLogger(__name__)`, hoy ausente).
Esto alinea `ws.py` con la convención ya establecida en el repo: el endpoint WS
hermano `chat_ws.py:186-187` ya hace exactamente
`except WebSocketDisconnect: pass` / `except Exception: log.exception(...)`, y
`ws_manager.py` loguea en todos sus handlers de excepción.

## Criterio de aceptación
- [x] Las excepciones inesperadas del endpoint `/ws` se loguean a nivel `exception`/`warning`
- [x] `WebSocketDisconnect` se sigue manejando en silencio (sin ruido en logs)
- [x] El servidor sigue sin crashear y la conexión se cierra vía `finally`
- [x] No se rompe ningún test existente

## Notas
Scope SOLO `ws.py:23`. El `chat_ws.py:186` que algunos hallazgos mencionaban
YA loguea con `log.exception(...)` — no hay nada que cambiar ahí. `conn` está
garantizado no-None en ese punto (el `if conn is None: return` ya se ejecutó),
así que no hace falta guard al loguear el `user_id`.

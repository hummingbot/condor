---
id: READ-016
title: Bare except enmascara SystemExit/KeyboardInterrupt en config_manager
category: readability
impact: low
effort: S
risk: low
status: done
files:
  - config_manager.py:479
commits:
  - "e779f72 (fix) delete_user_preference persiste el borrado + bare except acotado en config_manager (CORR-003, READ-016)"
created: 2026-06-10
---

## Problema
La línea 479 de `config_manager.py` usa un `except:` desnudo en un bloque de cleanup. Captura todo,
incluidos `SystemExit` y `KeyboardInterrupt`, lo que dificulta el debugging y es inconsistente con el
resto del archivo, que usa `except Exception:` para el mismo propósito (líneas 378, 384).

## Solución propuesta
Reemplazar el `except:` desnudo por `except Exception:` (manteniendo el `pass`/cleanup actual), para no
tragar señales de control y documentar mejor la intención.

## Criterio de aceptación
- [x] No queda ningún `except:` desnudo en `config_manager.py`
- [x] El bloque sigue tragando solo errores normales (`Exception`), no `SystemExit`/`KeyboardInterrupt`
- [x] No se rompe ningún test existente

## Notas
Riesgo y esfuerzo mínimos; alinea con el patrón ya usado en líneas 378 y 384 del mismo archivo.

**Cierre (e779f72):** Resuelto: `except:` → `except Exception:` (verificado por grep que no quedan bare excepts).

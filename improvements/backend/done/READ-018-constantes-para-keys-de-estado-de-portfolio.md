---
id: READ-018
title: Claves de estado del portfolio como string literals dispersos por el handler
category: readability
impact: medium
effort: M
risk: low
status: done
files:
  - handlers/portfolio.py:284
commits:
  - "a61be87 (refactor) dedup is_gateway_network, consolidar clear_*_state, constantes de estado del portfolio (ARCH-011, ARCH-014, READ-018)"
created: 2026-06-10
---

## Problema
Claves de estado como `'portfolio_text_message_id'`, `'portfolio_chat_id'`, `'portfolio_balances'`
aparecen como string literals hardcodeados en 20+ lugares de `handlers/portfolio.py` (líneas 284-290,
360-362, 398, 412-443, 460-461, 525-528). Un typo (`'portfolio_text_messge_id'`) no levanta error y
falla en silencio; renombrar una clave exige editar múltiples ubicaciones; y no hay type hints que
documenten la estructura esperada de `context.user_data`.

## Solución propuesta
Definir las claves en un único lugar (constantes a nivel de módulo, o un `TypedDict` que tipe la
porción de `context.user_data` del portfolio, siguiendo el patrón de `handlers/config/user_preferences.py`)
y reemplazar los literales por esas referencias en todo `portfolio.py`.

## Criterio de aceptación
- [x] Todas las claves de estado del portfolio se definen en un solo lugar
- [x] No quedan string literals repetidos para esas claves en `portfolio.py`
- [x] Un typo en el nombre de clave es detectable (referencia a constante / type checking)
- [x] Comportamiento sin cambios para el usuario

## Notas
Mejora de mantenibilidad, no bug. `user_preferences.py` ya usa `TypedDict`, así que hay un patrón del
repo que reutilizar.

**Cierre (a61be87):** Resuelto: 7 constantes de módulo (`KEY_*`) reemplazan los string literals en portfolio.py; grep confirma que los literales solo viven en las definiciones.

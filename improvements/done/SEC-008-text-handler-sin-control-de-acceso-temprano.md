---
id: SEC-008
title: handle_all_text_input procesa inputs de estado sin revalidar aprobación del usuario
category: security
impact: medium
effort: M
risk: medium
status: done
files:
  - handlers/config/__init__.py:215
commits:
  - "60582c5 (fix) aplicar @restricted a handle_all_text_input (SEC-008)"
created: 2026-06-10
---

## Problema
`handle_all_text_input()` (línea 215 de `handlers/config/__init__.py`) enruta TODOS los inputs de
estado (API keys, servidor, gateway) hacia `handle_api_key_input`, `handle_server_input`,
`handle_gateway_input`, etc., **sin** un checkpoint de control de acceso al inicio. Si un usuario logra
quedar con un estado `awaiting_*` en su `context.user_data`, puede enviar texto que será procesado por
los sub-handlers sin revalidar su rol/aprobación. Los comandos de entrada (`/keys`, `/servers`) tienen
`@restricted`, pero el message handler que consume el input no.

## Solución propuesta
Añadir un checkpoint de acceso al inicio de `handle_all_text_input()` (decorador `@restricted` o
chequeo explícito de aprobación), y como defensa en profundidad replicarlo al inicio de los
sub-handlers (`handle_api_key_input`, `handle_api_key_config_input`, `handle_server_input`,
`handle_gateway_input`). Reusar el mecanismo de `utils/auth.py`.

## Criterio de aceptación
- [x] `handle_all_text_input()` rechaza input de usuarios no aprobados/bloqueados antes de enrutar
- [ ] Los sub-handlers de input revalidan acceso *(diferido — ver Notas)*
- [x] Usuarios aprobados conservan el flujo normal
- [x] No se rompe ningún test existente

## Notas
El vector es indirecto (requiere que el usuario tenga un estado `awaiting_*` seteado), pero el message
handler es un límite de confianza que hoy carece de control de acceso explícito.

**Cierre (60582c5):** Resuelto el checkpoint principal: `@restricted` en `handle_all_text_input` (rechaza no-aprobados antes de enrutar). DIFERIDO: la defensa-en-profundidad en los sub-handlers (criterio opcional) no se aplicó para no invadir archivos de otros agentes; queda como hardening adicional futuro.

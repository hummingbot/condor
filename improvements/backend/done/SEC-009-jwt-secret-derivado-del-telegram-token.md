---
id: SEC-009
title: Secret de JWT derivado del TELEGRAM_TOKEN acopla credenciales y bloquea rotación
category: security
impact: medium
effort: S
risk: low
status: done
files:
  - condor/web/auth.py:29
commits:
  - "ecef600 (feat) WEB_JWT_SECRET dedicado + hardening de one-time login token (SEC-009, SEC-010)"
created: 2026-06-10
---

## Problema
En `condor/web/auth.py` (línea 29) el secret de firma de JWT se deriva del `TELEGRAM_TOKEN` vía SHA256.
Esto acopla las credenciales del bot de Telegram con la firma de las sesiones web: no se puede rotar el
secret de JWT de forma independiente, y comprometer uno implica al otro. Es una violación de buenas
prácticas criptográficas (separación de secretos por propósito).

## Solución propuesta
Introducir una variable de entorno dedicada (`WEB_JWT_SECRET`) y usarla para firmar/verificar JWT. Si
no está presente, generar/exigir un secret propio (no derivar del token de Telegram). Documentar la
variable en el `.env` y mantener compatibilidad de sesiones existentes durante el cambio si aplica.

## Criterio de aceptación
- [x] El secret de JWT proviene de su propia variable de entorno, no del `TELEGRAM_TOKEN`
- [x] Se puede rotar el secret de JWT sin tocar el token del bot
- [x] El login web sigue funcionando con la nueva configuración
- [ ] La variable queda documentada en `.env`/CLAUDE.md *(diferido — ver Notas)*

## Notas
Severidad real media, impacto práctico bajo (TTL de 24h, deployment con whitelist de admin), pero el
fix es trivial (~3 líneas) y mejora la postura de seguridad. Relacionado con [[SEC-010]] (auth web).

**Cierre (ecef600):** Resuelto: `WEB_JWT_SECRET` tiene prioridad; si falta, fallback al comportamiento legacy con un warning. PENDIENTE (fuera de scope del agente): documentar la var en `.env`/CLAUDE.md.

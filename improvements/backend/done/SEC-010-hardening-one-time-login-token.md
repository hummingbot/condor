---
id: SEC-010
title: Store de one-time login token sin GC, rate limiting ni protección de reuso
category: security
impact: medium
effort: M
risk: medium
status: done
files:
  - condor/web/auth.py:104
  - condor/web/routes/auth.py:20
commits:
  - "ecef600 (feat) WEB_JWT_SECRET dedicado + hardening de one-time login token (SEC-009, SEC-010)"
created: 2026-06-10
---

## Problema
El store de tokens de login de un solo uso en `condor/web/auth.py` (líneas 104-129) tiene tres fallas:
1. **Memory leak**: los tokens expirados solo se limpian en `create_login_token()`, no en
   `redeem_login_token()`; si un token nunca se redime, queda en memoria.
2. **Sin rate limiting**: `redeem_login_token()` no limita intentos, permitiendo fuerza bruta contra la
   ventana de validez (5 min) sin protección.
3. **Token por URL GET** (`?token=...`, `condor/web/routes/auth.py:20`): explotable vía CSRF si un
   atacante obtiene un token válido.

## Solución propuesta
- Añadir garbage collection explícito de tokens expirados también en `redeem_login_token()` (o un
  barrido periódico).
- Añadir rate limiting por `user_id`/IP en `redeem_login_token()`.
- Mitigar el token-en-URL (preferir POST/header, o un token CSRF de un solo uso ligado a la sesión).

## Criterio de aceptación
- [x] Tokens expirados se eliminan del store aunque nunca se rediman
- [x] `redeem_login_token()` aplica rate limiting ante intentos repetidos
- [ ] El canje de token no es trivialmente explotable por CSRF (token no viaja solo en URL GET, o hay CSRF token) *(diferido — ver Notas)*
- [x] El login web legítimo sigue funcionando

## Notas
Tres mitigaciones sobre la misma función/flujo de login. La de CSRF (canje por URL) es la de mayor
esfuerzo y puede separarse en un item propio si se prefiere. Relacionado con [[SEC-009]].

**Cierre (ecef600):** Resuelto (a) GC de expirados en `redeem_login_token` y (b) rate limiting por user_id. (c) CSRF MITIGADO de forma simple (el redeem ya es POST; se añadió `Referrer-Policy: no-referrer`/`Cache-Control: no-store` y se retira el token de la URL en el frontend), pero el rediseño completo con CSRF token dedicado queda DIFERIDO por alto riesgo de romper el login.

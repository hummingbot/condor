---
id: SEC-038
title: ApiKeysSettings renderiza campos de credenciales no-'secret'/'password' (ej. api_key) como inputs de texto plano
category: security
impact: low
effort: S
risk: low
status: todo
files:
  - frontend/src/components/settings/ApiKeysSettings.tsx:122
  - frontend/src/components/settings/ApiKeysSettings.tsx:246
commits: []
created: 2026-06-10
---

## Problema
`isSecret` se deriva (`ApiKeysSettings.tsx:122`) solo cuando el type del campo contiene `'secret'` o
la key contiene `'secret'`/`'password'`. Campos de credenciales de connectors comúnmente llamados
`api_key`, variantes de `secret_key`, `passphrase` o `private_key` que no matchean esos substrings se
renderizan con `type="text"` (línea 246), así que material sensible se muestra en texto plano en
pantalla y queda elegible para autofill/retención de valores del navegador. Para una app de trading
esto expone API keys de exchange over-the-shoulder y en el historial de formularios. (`api_key` es el
nombre de credencial más común en connectors de Hummingbot y NO contiene `secret`/`password`.)

## Solución propuesta
Ampliar la heurística de secreto para cubrir nombres comunes de credenciales: tratar también keys/types
con `key`, `passphrase`, `private`, `token`, `mnemonic`, `seed` como secret, y/o renderizar todo campo
de credencial como input `password` con un toggle show/hide (son todos sensibles por naturaleza).
Agregar `autoComplete='off'` (o `'new-password'`) al input.

## Criterio de aceptación
- [ ] Campos como `api_key`/`secret_key`/`passphrase`/`private_key` se renderizan enmascarados por defecto
- [ ] Los inputs de credenciales usan `autoComplete='off'` / `'new-password'`
- [ ] (Opcional) Un toggle show/hide permite revelar un valor intencionalmente

## Notas
Distinto de SEC-006/SEC-008 del backlog general (handler de Telegram). `secret`/`passphrase` sí quedan
ocultos hoy; el gap es `api_key` y similares.

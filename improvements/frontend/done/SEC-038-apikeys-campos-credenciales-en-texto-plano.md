---
id: SEC-038
title: ApiKeysSettings renderiza campos de credenciales no-'secret'/'password' (ej. api_key) como inputs de texto plano
category: security
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/components/settings/ApiKeysSettings.tsx:122
  - frontend/src/components/settings/ApiKeysSettings.tsx:246
commits:
  - 558a22a
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
- [x] Campos como `api_key`/`secret_key`/`passphrase`/`private_key` se renderizan enmascarados por defecto
- [x] Los inputs de credenciales usan `autoComplete='off'` / `'new-password'`
- [ ] (Opcional) Un toggle show/hide permite revelar un valor intencionalmente

## Notas
Distinto de SEC-006/SEC-008 del backlog general (handler de Telegram). `secret`/`passphrase` sí quedan
ocultos hoy; el gap es `api_key` y similares.

### Resolución
Se extrajo la heurística a un predicado `isCredentialField(key, type)` a nivel de módulo que matchea
por substring (sobre `key` + `type`) contra `CREDENTIAL_FIELD_PATTERNS`:
`secret`, `password`, `passphrase`, `key`, `token`, `private`, `mnemonic`, `seed`.

Elección del predicado: substring sobre el nombre del campo (no solo tipos exactos), porque los config
maps de connectors de Hummingbot nombran las credenciales de forma variada (`api_key`, `secret_key`,
`api_token`, `private_key`...) y casi todas contienen `key`/`token`/`private`. Campos no sensibles
(p.ej. `subaccount`, `account_name`, labels) no contienen ninguno de esos substrings y siguen como
`type="text"`. Se mantuvo la API/forma del campo (`isSecret`) intacta; solo cambió su derivación.

El input ahora setea `autoComplete="new-password"` para campos secretos y `"off"` para el resto,
evitando autofill/retención del navegador.

Toggle show/hide: no implementado (marcado opcional, fuera del scope mínimo).

### Fuera de scope (no abordado)
- Toggle show/hide para revelar valores intencionalmente.
- La lista de credenciales ya configuradas (sección "Main list") no muestra valores, así que no
  requiere enmascarado.

---
id: SEC-006
title: Valor de callback select: en api_keys se almacena sin validar contra allowed_values
category: security
impact: high
effort: M
risk: medium
status: done
files:
  - handlers/config/api_keys.py:447
  - handlers/config/api_keys.py:1031
  - handlers/config/api_keys.py:611
commits:
  - "241e260 (fix) validar callback select contra allowed_values en api_keys (SEC-006)"
created: 2026-06-10
---

## Problema
En `handlers/config/api_keys.py`, la rama `select:` (línea 447) extrae el valor con
`selected_value = action_data.replace("select:", "")` y lo pasa a
`_handle_field_value_selection(...)`, que en la línea 1031 lo guarda directamente
(`values[awaiting_field] = selected_value`) **sin** validarlo contra
`field_metadata[awaiting_field]["allowed_values"]`. La función hermana
`handle_api_key_config_input()` (línea 611) **sí** valida (`if allowed_values and new_value not in
allowed_values: rechaza`). Esta asimetría permite craftear un `callback_data` como
`api_key_select:<valor_no_permitido>` para campos `Literal` y bypassear la whitelist de la UI
(p. ej. forzar un valor fuera del conjunto permitido).

## Solución propuesta
Antes de almacenar en la línea 1031, replicar la validación de la línea 611: si el campo es de tipo
`Literal`, rechazar (`query.answer("❌ Invalid value")` + `return`) cuando `selected_value` no esté en
`allowed_values`; si es `bool`, normalizar. Centralizar idealmente la validación en una sola función
usada por ambos caminos (input de texto y selección por callback) para que no vuelvan a divergir.

## Criterio de aceptación
- [x] Un `select:` con valor fuera de `allowed_values` es rechazado y no se persiste
- [x] Campos `bool` se normalizan correctamente
- [x] Ambos caminos (texto y callback) comparten la misma validación
- [x] No se rompe ningún flujo legítimo de configuración de API keys

## Notas
Defensa en profundidad: aunque el backend pueda validar, el frontend no debe confiar en el callback.
Revisar también líneas 234, 327-328, 829, 898, 920 donde valores se reusan en callbacks posteriores.

**Cierre (241e260):** Resuelto: la rama `select:` ahora valida `Literal` contra `allowed_values` (y normaliza `bool`) reutilizando la misma lógica que `handle_api_key_config_input`, cortando antes de persistir.

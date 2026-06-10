---
id: SEC-007
title: /servers/{name}/status permite enumeración de servidores por falta de control de acceso
category: security
impact: high
effort: M
risk: medium
status: done
files:
  - condor/web/routes/servers.py:38
  - condor/web/routes/bots.py:166
commits:
  - "c740e88 (fix) control de acceso y anti-enumeracion en /servers/{name}/status (SEC-007)"
created: 2026-06-10
---

## Problema
El endpoint `/servers/{name}/status` (líneas 38-47 de `condor/web/routes/servers.py`) no aplica
control de acceso por servidor y permite **enumeración**: si el usuario no tiene permiso responde
HTTP 200 + JSON igualmente; si el servidor no existe, `get_client()` lanza `ValueError` que se traduce
a HTTP 500. Un atacante distingue "servidor válido sin permiso" (200) de "servidor inexistente" (500),
filtrando qué nombres existen. El patrón correcto ya está en `bots.py` (líneas 166-167), que rechaza
con `HTTPException(status_code=403)`.

## Solución propuesta
Validar el acceso del usuario al servidor (`cm.has_server_access(...)`) antes de llamar a
`get_client()`. Para no filtrar existencia, devolver **404** tanto cuando el servidor no existe como
cuando existe pero el usuario no tiene permiso (respuesta indistinguible). Capturar el `ValueError` de
`get_client()` y mapearlo también a 404.

## Criterio de aceptación
- [x] Sin permiso o servidor inexistente devuelven la **misma** respuesta (404), no 200/500
- [x] Usuario con permiso sigue recibiendo el status correcto
- [x] No es posible distinguir servidor válido-sin-permiso de inexistente
- [x] No se rompe ningún test existente

## Notas
Alinear con el patrón de rechazo de `bots.py:166-167`. Revisar si otras rutas de `servers.py` tienen
la misma laguna.

**Cierre (c740e88):** Resuelto: se valida `has_server_access` antes de `get_client()`; sin permiso y servidor inexistente devuelven el MISMO 404 (`ValueError`→404), eliminando la enumeración.

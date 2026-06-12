---
id: SEC-019
title: Validar has_server_access en subscribe del WebSocket (fuga cross-server)
category: security
impact: high
effort: M
risk: high
status: done
files:
  - condor/web/ws_manager.py:362
  - condor/web/routes/ws.py:11
  - config_manager.py:699
commits:
  - "4299b43 (fix) validar has_server_access en subscribe del WS para evitar fuga cross-server (SEC-019)"
created: 2026-06-10
---

## Problema
La conexión WebSocket autentica al usuario una sola vez en `connect()`
(`ws_manager.py:285-304`): valida el JWT y que el rol sea `USER`/`ADMIN`, y
guarda `conn.user_id`. Pero en `handle_message()` la rama `subscribe`
(`ws_manager.py:362-364`) hace `conn.channels.add(channel)` con el `channel`
provisto por el cliente **sin verificar acceso al servidor**. Los canales
codifican el servidor en el nombre (`portfolio:<server>`, `bots_ws:<server>`,
`executors:<server>`, `prices:<server>:<connector>:<pair>`,
`candles:<server>:...`) y los streams parsean ese `server_name` para abrir un
cliente con `cm.get_client(server_name)` y empujar los datos de vuelta al
suscriptor.

El modelo de acceso es **por servidor**: `get_server_permission()`
(`config_manager.py:677-697`) solo concede acceso a admins, dueños del server o
usuarios en `shared_with`. Todos los endpoints REST lo respetan con
`cm.has_server_access(user.id, name)` (p.ej. `bots.py:184` y ~16 sitios más),
pero el path WS lo saltea por completo. Un usuario aprobado que solo tiene
acceso a `staging` puede mandar `{"action":"subscribe","channel":"bots_ws:production"}`
y recibir en vivo el portfolio/bots/executors/positions de `production`
(IDOR / autorización a nivel de objeto rota).

## Solución propuesta
En `handle_message()`, en la rama `subscribe` (antes de
`conn.channels.add(channel)`), extraer el `server_name` del canal y validar
`cm.has_server_access(conn.user_id, server_name)` (firma en
`config_manager.py:699`, default `TRADER`). Si no tiene acceso: loggear el
intento y rechazar (no agregar el canal; opcionalmente enviar un mensaje de
error al cliente). El chokepoint de `subscribe` es suficiente y autoritativo;
los `_ensure_*`/`_subscribe_sds` quedan como defensa en profundidad opcional.
Centralizar la extracción de `server_name` desde el canal en un helper para no
repetir el `channel.split(":")[1]` por cada tipo de canal.

## Criterio de aceptación
- [x] Un usuario solo puede suscribirse a canales de servidores a los que tiene acceso explícito
- [x] Los intentos de suscripción no autorizados se loggean y se rechazan (el canal no se agrega a `conn.channels`)
- [x] Test: el usuario A no recibe datos del servidor B tras intentar `subscribe` a `bots_ws:<server_B>` (test runtime ad-hoc con `get_config_manager` stubbeado: deny cross-server, deny canal sin server, allow servidor accesible)
- [x] No se rompe ninguna suscripción legítima existente (todos los canales WS son server-scoped en `parts[1]`; el allow agrega el canal igual que antes)

## Notas
`risk: high` y toca control de acceso: confirmar con el usuario antes de editar.
La re-validación periódica de permisos a mitad de sesión (revocación en caliente)
y el cierre del WS al perder acceso son una mejora complementaria fuera de este
item — anotar como follow-up si interesa. Sigue el patrón de autorización REST
ya establecido en el repo (ver SEC-006/SEC-007/SEC-008 ya cerrados).

**Implementación (cierre):** chokepoint único en la rama `subscribe` de
`handle_message`, antes de `conn.channels.add(channel)`. Helper estático
`_server_from_channel(channel)` extrae el server de `parts[1]` (None si el canal
no es server-scoped). Si no hay server o `cm.has_server_access(conn.user_id,
server)` es False (default `TRADER`), se loguea el intento y se rechaza (no se
agrega el canal, no se arranca ningún stream). Fail-closed: un canal sin segmento
de server se rechaza (todos los canales reales son server-scoped). No se editó
`routes/ws.py` (el item lo listaba como referencia del path WS, no requería
cambios) ni `config_manager.py` (solo se consume `has_server_access`). La
revocación en caliente queda como follow-up.

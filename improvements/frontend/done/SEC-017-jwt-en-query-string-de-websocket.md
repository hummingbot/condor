---
id: SEC-017
title: El JWT de auth viaja en el query string de la URL del WebSocket — se filtra por logs e historial
category: security
impact: medium
effort: M
risk: medium
status: done
files:
  - frontend/src/lib/websocket.ts:19
  - frontend/src/hooks/useChatSocket.ts:107
commits:
  - 2a48851
created: 2026-06-10
---

## Problema
El bearer JWT se embebe en el query string de la URL del WebSocket en dos lugares:
`websocket.ts:19` (`this.url = `${proto}//${window.location.host}/api/v1/ws?token=${token}``) y
`useChatSocket.ts:107` (`/api/v1/ws/chat?token=${token}`). Los tokens en URLs se escriben
rutinariamente en logs de reverse-proxy/access, traces de APM/observabilidad y cualquier
intermediario, y están más expuestos a fugas por Referer/historial que los headers. A diferencia
del path REST (`api.ts:9-11`) que correctamente usa el header `Authorization`, el upgrade WS lleva
el secreto en la URL. Combinado con el token de larga vida en localStorage (sin expiry
cliente), una sola línea de log expone una credencial reutilizable.

## Solución propuesta
Dejar de poner el token crudo en la URL. Preferido: enviar el token en el primer mensaje WS tras
`onopen` (un frame de auth/subscribe) y que el backend autentique en ese frame en vez del query
param — el cliente ya envía frames estructurados (`websocket.ts:_send`, línea 110, con `onopen` en
70). Alternativa: que el server emita un ticket WS de un solo uso y vida corta vía un POST
autenticado (header Authorization) y pasar ese ticket opaco en la URL, de modo que una línea de log
filtrada sea inútil tras el primer uso/expiry. Aplicar el mismo fix a ambos WS (main y chat).

## Criterio de aceptación
- [x] La URL de conexión del WebSocket ya no contiene el JWT de larga vida en su query string
- [x] La autenticación ocurre vía el header `Sec-WebSocket-Protocol` (subprotocolo), no in-band ni ticket
- [x] Tanto `/api/v1/ws` (`websocket.ts`) como `/api/v1/ws/chat` (`useChatSocket.ts`) se actualizan consistentemente y siguen conectando/reconectando

## Notas

### Enfoque elegido: subprotocolo `Sec-WebSocket-Protocol`
Los navegadores no pueden setear headers custom en el handshake WS, pero el segundo
argumento del constructor `WebSocket(url, protocols)` sí controla el header
`Sec-WebSocket-Protocol`. El cliente ofrece `["condor-jwt", <jwt>]` y el backend lee
el token del segundo valor. Se eligió este enfoque sobre el frame in-band y sobre el
ticket de un solo uso porque es el más simple y verificable, no añade un round-trip
HTTPS extra ni estado server-side, y saca el token de la URL en un solo cambio.

Detalle clave: el servidor **debe** devolver el subprotocolo seleccionado en
`ws.accept(subprotocol=...)` o el navegador rechaza el handshake. Se devuelve el
centinela `"condor-jwt"` (nunca el token) para evitar reflejar el JWT y para no
arriesgar caracteres no-token (el JWT contiene `.`).

### Fallback backward-compatible (deprecado)
El query param `?token=` se mantiene como fallback en ambos endpoints
(`extract_ws_token()` en `condor/web/auth.py`) para no romper sesiones vivas ni
clientes viejos durante el rollout. Una vez que todos los clientes usen el
subprotocolo, se puede eliminar el fallback (y de paso reduce la superficie de fuga).

### Cambios
- Frontend: `CondorWebSocket` (`websocket.ts`) y `useChatSocket.ts` quitan `?token=`
  y pasan `[WS_AUTH_SUBPROTOCOL, token]` como segundo arg del `WebSocket`.
- Backend: helper `extract_ws_token()` compartido + `ws.py`, `chat_ws.py` y
  `WebSocketManager.connect()` aceptan el subprotocolo y hacen `accept(subprotocol=...)`.

### Verificación
- `npx tsc -b` exit 0; eslint en baseline (96 errores / 23 warnings, sin nuevos).
- Test de integración contra el router real `/ws`: handshake por subprotocolo conecta
  (centinela `condor-jwt` reflejado), fallback por query conecta, token inválido se rechaza.

Mismo dominio que [[ARCH-010]] y [[SEC-016]].

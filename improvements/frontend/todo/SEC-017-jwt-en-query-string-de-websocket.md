---
id: SEC-017
title: El JWT de auth viaja en el query string de la URL del WebSocket — se filtra por logs e historial
category: security
impact: medium
effort: M
risk: medium
status: todo
files:
  - frontend/src/lib/websocket.ts:19
  - frontend/src/hooks/useChatSocket.ts:107
commits: []
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
- [ ] La URL de conexión del WebSocket ya no contiene el JWT de larga vida en su query string
- [ ] La autenticación ocurre vía un frame de auth in-band o un ticket de un solo uso y vida corta
- [ ] Tanto `/api/v1/ws` (`websocket.ts`) como `/api/v1/ws/chat` (`useChatSocket.ts`) se actualizan consistentemente y siguen conectando/reconectando

## Notas
Requiere cambio coordinado en el backend (`condor/web/`). Mismo dominio que [[ARCH-010]] y [[SEC-016]].

---
id: ARCH-010
title: Centralizar token de auth y fetch con Bearer — literal "condor_token" y header duplicados en 4 archivos
category: architecture
impact: high
effort: S
risk: low
status: done
files:
  - frontend/src/lib/api.ts:1
  - frontend/src/lib/auth.ts:24
  - frontend/src/components/chat/ChatInput.tsx:7
  - frontend/src/components/chat/ChatInput.tsx:284-286
  - frontend/src/components/routines/RoutineResultView.tsx:22-24
commits:
  - "e10d0f4 (refactor) centralizar token de auth en lib/auth-token (ARCH-010)"
created: 2026-06-10
---

## Problema
El nombre de la clave de localStorage del JWT y la lógica de construir el header
`Authorization` están copiados en varios sitios. `api.ts:1` define
`const TOKEN_KEY = "condor_token"` pero NO lo exporta. `auth.ts:24` lo redefine.
`ChatInput.tsx:7` lo vuelve a definir y en `ChatInput.tsx:284-286` hace un `fetch` crudo de
`/api/v1/transcribe` con `headers: token ? { Authorization: "Bearer "+token } : {}`.
`RoutineResultView.tsx:22-24` (componente `AuthImage`) vuelve a leer
`localStorage.getItem("condor_token")` y arma el mismo header a mano. Son llamadas fetch crudas
en componentes que reimplementan exactamente lo que `apiFetch` (`api.ts:3`) ya hace — pero no
pueden reusarlo porque `apiFetch` fuerza `Content-Type: application/json` y `res.json()`,
mientras transcribe usa FormData y AuthImage espera un blob. Si la clave del token o el esquema
de auth cambian, hay que tocar 4 archivos y es fácil que uno quede roto.

## Solución propuesta
Exportar desde `lib/api.ts` (o un nuevo `lib/auth-fetch.ts`) una constante `TOKEN_KEY` y un
helper `authHeaders()` que devuelva `{ Authorization: "Bearer <token>" }` (o `{}` si no hay
token). Reutilizarlo en `apiFetch`, `auth.ts`, ChatInput (transcribe) y RoutineResultView
(AuthImage). Para los casos FormData/blob exponer además un `authFetch(path, init)` de bajo nivel
que solo inyecte el header de auth sin forzar Content-Type JSON.

## Criterio de aceptación
- [x] El literal `"condor_token"` aparece exactamente una vez en `src/` (en la definición de `TOKEN_KEY`)
- [x] `ChatInput.tsx` y `RoutineResultView.tsx` no llaman `localStorage.getItem` ni construyen el header Bearer a mano; usan el helper compartido
- [x] `auth.ts` importa `TOKEN_KEY` en vez de redefinirlo
- [x] Transcribir audio y mostrar imágenes autenticadas (`AuthImage`) siguen funcionando (refactor sin cambio de comportamiento; `tsc -b` limpio)

## Notas
Relacionado con [[SEC-016]] (token en query string de WS) y [[SEC-017]] (iframe sin sandbox): los
tres tocan la superficie del token de auth.

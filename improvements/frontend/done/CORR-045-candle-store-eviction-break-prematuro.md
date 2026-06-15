---
id: CORR-045
title: candle-store _enforceMaxCollections aborta la evicción LRU en la primera colección activa
category: correctness
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/lib/candle-store.ts:267-280
commits:
  - "2dd496b (fix) candle-store evicta LRU saltando colecciones activas (CORR-045)"
created: 2026-06-10
---

## Problema
En `_enforceMaxCollections` (`candle-store.ts:267-280`) el loop de evicción hace `shift()` de la key
LRU-front, y si tiene subscribers activos (`refCount > 0`) la re-inserta y hace `break`. El comentario
dice "If all are active, just break to avoid infinite loop", pero el código **rompe en la PRIMERA
colección activa** que encuentra al frente de `accessOrder`, no solo cuando TODAS son activas. Cuando una
key activa queda al frente, la función no evicta nada aunque haya colecciones inactivas (evictables) más
atrás en la cola. El cap `MAX_COLLECTIONS = 20` no se respeta: `this.collections` puede crecer por
encima del techo durante navegación activa, hasta que el `_cleanupIdle` de 60s (umbral idle de 10 min)
las recupera.

> Nota del verificador: el mecanismo descrito en el hallazgo está **invertido** y la severidad
> sobreestimada. `_touchAccess` mueve las keys activas al FONDO (push), y el `shift()` saca del FRENTE,
> así que normalmente el frente tiene colecciones inactivas que SÍ se evictan. El `break` solo se dispara
> en el corner case de una suscripción activa "silenciosa" (suscrita pero sin updates, nunca re-touched)
> que derive al frente. Con pocos canales activos simultáneos (~1-3) y `_cleanupIdle` reclamando idle,
> el peor caso es un overshoot **acotado y transitorio**, no crecimiento ilimitado. Aun así es un defecto
> real de la evicción.

## Solución propuesta
Saltar las colecciones activas en vez de romper: re-insertar la key activa y `continue` al siguiente
candidato; detenerse solo cuando toda entrada restante es activa. Concretamente, iterar sobre un snapshot
de `accessOrder`, borrar la primera colección no-activa (`refCount === 0`) encontrada, y parar cuando no
queden evictables — garantizando terminación sin borrar colecciones activas. Agregar un test que cubra:
key activa al frente + key inactiva detrás ⇒ se evicta la inactiva y `collections.size < MAX_COLLECTIONS`.

## Criterio de aceptación
- [ ] Con una suscripción activa al frente del LRU y el count en/por encima de `MAX_COLLECTIONS`, crear una colección nueva evicta una inactiva de atrás (`size < MAX_COLLECTIONS`)
- [ ] Cuando toda colección tiene `refCount > 0`, `_enforceMaxCollections` termina sin loop infinito y sin borrar activas
- [ ] Un test de regresión asegura que `collections.size` nunca excede `MAX_COLLECTIONS` tras suscribir a >20 canales distintos mientras uno permanece activo

## Notas
Distinto del item del scope general `done/PERF-004` (eviction en candle buffer del backend Python): este
es el `candle-store.ts` del frontend (TS), no cubierto por ningún item del scope frontend.

---
id: CORR-026
title: EditorTab FileContentLoader actualiza estado del padre durante el render (anti-patrón)
category: correctness
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/pages/tabs/EditorTab.tsx:866-887
  - frontend/src/pages/tabs/EditorTab.tsx:769-784
commits:
  - "<pending> (fix) FileContentLoader maneja data en useEffect, no en render (CORR-026)"
created: 2026-06-10
---

## Problema
`FileContentLoader` llama `onLoaded`/`onError` directamente en su cuerpo de render
(`EditorTab.tsx:866-887`), no dentro de un `useEffect`. Esos callbacks (definidos en 769-784)
invocan `setOpenTabs` sobre el padre `EditorTab` mientras el hijo está renderizando. Llamar a un
setter de estado del padre mientras un hijo renderiza es el clásico anti-patrón "Cannot update a
component while rendering a different component". Funciona hoy solo porque un `loadedRef` guarda
contra re-entrada, pero depende de que React tolere el warning y de que el resultado de la query
esté disponible exactamente durante el render. Es frágil bajo doble invocación de StrictMode y
concurrent rendering, y acopla la carga al timing de render en vez de a efectos.

## Solución propuesta
Mover el manejo de data a `useEffect` dentro de `FileContentLoader` keyed sobre
`controllerQuery.data`/`isError` y `configQuery.data`/`isError`, llamando `onLoaded`/`onError`
desde el efecto (guardado por `loadedRef`). Esto difiere el `setState` del padre a la fase de
commit, eliminando el update en fase de render.

## Criterio de aceptación
- [x] `onLoaded`/`onError` solo se llaman dentro de `useEffect`, nunca directo en el render body de `FileContentLoader`
- [x] Abrir un tab de controller o config sigue cargando su contenido sin el warning "update a component while rendering" en consola
- [x] El doble render de StrictMode no carga un tab dos veces ni lanza error

## Notas
Mismo archivo que [[CORR-030]] (no invalidar `controller-source` tras guardar) y [[PERF-025]].

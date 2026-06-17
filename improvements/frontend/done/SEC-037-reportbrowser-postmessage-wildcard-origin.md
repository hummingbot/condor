---
id: SEC-037
title: ReportBrowser postea el tema al iframe de report con target origin wildcard '*'
category: security
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/components/routines/ReportBrowser.tsx:233
commits:
  - 7082e4b
created: 2026-06-10
---

## Problema
`ReportBrowser.tsx:233` llama `iframe.contentWindow?.postMessage({ type: 'set-theme', theme }, '*')`.
El target origin wildcard `'*'` significa que el mensaje se entrega a cualquier documento que ocupe el
iframe, sin importar su origen. El HTML de los reports es contenido generado por usuario/agente servido
desde `/reports/<filename>`; si un report alguna vez navega el iframe a un origen de terceros (o el
contenido del iframe es influenciado por atacante), ese origen recibe el `postMessage`. Aunque el
payload acá es solo un string de tema (baja sensibilidad), el wildcard es un foot-gun latente y se
combina con el iframe sin sandbox ya reportado en [[SEC-016]].

## Solución propuesta
Usar el propio origen de la app como target explícito:
`postMessage({ type: 'set-theme', theme }, window.location.origin)`. Como el `src` del iframe es
same-origin (`/reports/...`), este es el target correcto y seguro, y previene que el mensaje se filtre
si el iframe alguna vez es redirigido cross-origin.

## Criterio de aceptación
- [x] El `postMessage` en `ReportBrowser` usa `window.location.origin` (u origen específico) en vez de `'*'`
- [x] La sincronización de tema al iframe same-origin del report sigue funcionando

## Notas
Pareja de [[SEC-016]] (iframe sin sandbox), distinto code location/concern. Hardening defense-in-depth,
fix de una línea.

Implementación: `postMessage(..., "*")` → `postMessage(..., window.location.origin)` en
`ReportBrowser.tsx:233`. Se verificó que el `src` del iframe es `/reports/<filename>` (relativo,
same-origin), por lo que `window.location.origin` es el target correcto. `npx tsc -b` exit 0; eslint
sin nuevos errores (4 errores `set-state-in-effect` preexistentes, no relacionados al cambio).

El hash en `commits:` puede quedar off-by-one respecto al HEAD final: al hacer el amend que escribe
el hash en el frontmatter, el contenido del commit cambia y por ende su hash. Se registra el hash del
commit que contiene el cambio de código + frontmatter cerrado, sin perseguir el punto fijo.

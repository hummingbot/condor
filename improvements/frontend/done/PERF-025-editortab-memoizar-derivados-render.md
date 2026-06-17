---
id: PERF-025
title: EditorTab recomputa activeTab/splitTab/tabsToLoad con find/filter en cada keystroke
category: performance
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/pages/tabs/EditorTab.tsx:604-606
  - frontend/src/pages/tabs/EditorTab.tsx:562-566
  - frontend/src/pages/tabs/EditorTab.tsx:483
commits:
  - 06ab536
created: 2026-06-10
---

## Problema
`EditorTab.tsx:604-606` computa `activeTab` (`openTabs.find`), `splitTab` (`openTabs.find`) y
`tabsToLoad` (`openTabs.filter`) directo en el cuerpo del render en cada render. Mientras se tipea
en el `CodeEditor`, `updateContent` reemplaza el array `openTabs` en cada keystroke (562-566), así
que estos scans re-corren en cada tecla. Además `controllerTypes = data?.controller_types ?? {}`
(línea 483) crea una identidad de objeto nueva cada render y se pasa a `UploadDialog`/`NewConfigDialog`,
anulando cualquier memoización ahí.

> Nota del verificador: `openTabs` suele ser un array chico, así que el costo de find/filter es bajo;
> el beneficio real es identidad estable para hijos memoizados y `tabsToLoad` (cuyo `.map` monta
> `FileContentLoader`). El `?? {}` solo aloca nuevo objeto cuando `controller_types` es nullish.

## Solución propuesta
Envolver `activeTab`/`splitTab`/`tabsToLoad` en `useMemo` keyed sobre `[openTabs, activeTabId]` /
`[openTabs, splitMode, splitTabId]`. Memoizar `controllerTypes` con
`useMemo(() => data?.controller_types ?? {}, [data])` para identidad estable entre renders que no
cambian `data`, evitando re-renders innecesarios de los diálogos.

## Criterio de aceptación
- [x] `activeTab`, `splitTab` y `tabsToLoad` se derivan vía `useMemo` con dep arrays correctas
- [x] `controllerTypes` tiene referencia estable cuando `data` no cambia
- [x] Edición, split view y carga lazy de contenido se comportan igual

## Notas
Impacto bajo. Mismo archivo que [[CORR-026]] y [[CORR-030]] (concerns distintos de EditorTab).

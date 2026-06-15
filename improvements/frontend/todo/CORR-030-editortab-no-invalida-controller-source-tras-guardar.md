---
id: CORR-030
title: EditorTab no invalida la query controller-source tras guardar un .py de controller (sirve contenido stale)
category: correctness
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/pages/tabs/EditorTab.tsx:328-335
  - frontend/src/pages/tabs/EditorTab.tsx:855
commits: []
created: 2026-06-10
---

## Problema
El `saveMutation.onSuccess` de `EditorPane` (`EditorTab.tsx:328-335`) solo invalida caches de
React Query cuando `tab.file.kind === 'config'` (invalida `available-configs` y `config-detail`).
Cuando se guarda un archivo fuente de controller vía `api.updateControllerSource` (línea 325), la
query `['controller-source', server, type, name]` (usada por `FileContentLoader` en la línea 855)
nunca se invalida. El tab abierto se ve correcto porque `markSaved` actualiza su `originalContent`
en memoria, pero al cerrar y reabrir ese archivo de controller, `FileContentLoader` sirve el fuente
cacheado de antes de la edición (con `staleTime: 5000` global en `App.tsx`, React Query lo trata
como fresco), ocultando los cambios recién guardados hasta un hard refresh.

## Solución propuesta
En `saveMutation.onSuccess`, añadir una rama `else` para controllers que llame
`queryClient.invalidateQueries({ queryKey: ['controller-source', server, tab.file.controllerType, tab.file.controllerName] })`,
reflejando la rama de config.

## Criterio de aceptación
- [ ] Guardar un controller `.py` y reabrirlo muestra el contenido guardado sin refrescar la página
- [ ] La query `controller-source` se invalida en el guardado exitoso de un controller

## Notas
Mismo archivo que [[CORR-026]] (setState en render) y [[PERF-025]] (memoización), concerns distintos.

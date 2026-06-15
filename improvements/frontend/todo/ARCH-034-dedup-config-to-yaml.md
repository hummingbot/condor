---
id: ARCH-034
title: La conversión config→YAML (filtrar keys internas + yaml.dump) está duplicada en 4 archivos con políticas inconsistentes
category: architecture
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/components/bots/ControllerBrowser.tsx:65-71
  - frontend/src/components/editor/EditorDialogs.tsx:364-369
  - frontend/src/pages/BotDetail.tsx:53-56
  - frontend/src/pages/tabs/EditorTab.tsx:877-881
commits: []
created: 2026-06-10
---

## Problema
La misma transformación "filtrar keys internas/id y luego `yaml.dump`" está re-implementada en cuatro
lugares con comportamiento genuinamente inconsistente:
- `ControllerBrowser.tsx:65-71` (`configToYaml`) quita `{id, controller_name, controller_type}` más
  keys con prefijo underscore, y dumpea con `sortKeys: true` + `noRefs: true`.
- `EditorDialogs.tsx:369`, `BotDetail.tsx:56` y `EditorTab.tsx:880` quitan solo `id` y dumpean con
  `sortKeys: false`.

Así, el mismo config de controller serializa distinto (orden de keys distinto, keys ocultas
distintas) según qué pantalla lo renderiza, y la regla de qué keys se ocultan vive en varios sitios
incompatibles. No existe helper compartido en `lib/`.

## Solución propuesta
Agregar un único helper en `lib/` (ej. `lib/configYaml.ts` exportando `configToYaml(config, opts)` y
el set canónico de hidden-keys) que filtre keys internas y dumpee con opciones acordadas. Reemplazar
las cuatro implementaciones inline por llamadas a él, eligiendo una política consistente de
`sortKeys`/hidden-keys (o un parámetro documentado por call site).

## Criterio de aceptación
- [ ] Un único helper de `lib/` realiza la serialización config→YAML y define el set de hidden-keys
- [ ] `ControllerBrowser`, `EditorDialogs`, `BotDetail` y `EditorTab` llaman al helper compartido
- [ ] El mismo config de controller produce YAML idéntico dondequiera que se muestre

## Notas
Combinar con [[PERF-024]] (memoizar `configToYaml` en ControllerBrowser): al centralizar, mantener la
memoización en el call site.

---
id: ARCH-034
title: La conversión config→YAML (filtrar keys internas + yaml.dump) está duplicada en 4 archivos con políticas inconsistentes
category: architecture
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/components/bots/ControllerBrowser.tsx:65-71
  - frontend/src/components/editor/EditorDialogs.tsx:364-369
  - frontend/src/pages/BotDetail.tsx:53-56
  - frontend/src/pages/tabs/EditorTab.tsx:877-881
commits: [1e86ce4]
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
- [x] Un único helper de `lib/` realiza la serialización config→YAML y define el set de hidden-keys
- [x] `ControllerBrowser`, `EditorDialogs`, `BotDetail` y `EditorTab` llaman al helper compartido
- [ ] El mismo config de controller produce YAML idéntico dondequiera que se muestre
      (NO se cumple a propósito — ver Notas: forzarlo sería un bug de save)

## Notas

### Reconciliación de política (corrección sobre la propuesta original)
El criterio "YAML idéntico dondequiera" es **incorrecto** y NO se implementó: las dos políticas no son
intercambiables, son inconsistentes por una razón legítima.

- `ControllerBrowser` es un editor de **partial-update sobre un controller corriendo en un bot**
  (`updateBotControllerConfig` → PUT del config parcial). Ahí `controller_name`/`controller_type` son
  read-only/derivados por el server, así que esconderlos del YAML es seguro y deseable. Usa además
  `sortKeys: true` y filtra keys `_`-prefijadas (vista de display).
- `EditorDialogs` (duplicar config), `BotDetail` y `EditorTab` son editores que **round-trippean** el
  YAML completo de vuelta al server al guardar (`createControllerConfig` / `updateConfig...`). Si les
  aplicáramos el set de hidden-keys de `ControllerBrowser`, el config guardado **perdería
  `controller_name`/`controller_type`** (y cualquier key `_*`) → bug real de pérdida de datos. Por eso
  estos solo quitan `id` (que se setea out-of-band) y mantienen orden de keys (`sortKeys: false`).

Decisión: **helper parametrizado** en `frontend/src/lib/configYaml.ts` con política por defecto
round-trip-safe (solo `id`, `sortKeys: false`) y opciones `hiddenKeys` / `stripUnderscore` / `sortKeys`
para la vista de display. Se exporta `CONTROLLER_HIDDEN_KEYS` como set canónico de las keys de identidad
del controller. **Ningún call site cambia su output** (cada uno conserva su comportamiento previo).

### Cambio de output documentado (no-op en la práctica)
Los 3 sites round-trip usaban `yaml.dump(..., { sortKeys:false, lineWidth:-1 })` **sin** `noRefs`. El
helper unificado siempre pasa `noRefs: true`. Es un cambio de output solo si el objeto tuviera
referencias compartidas (anchors/aliases YAML); los configs vienen de respuestas JSON de la API (sin
aliasing), así que es un no-op real y estrictamente más seguro para texto editable. Cambio correcto e
intencional.

### Fuera de alcance
Combinar con [[PERF-024]] (memoizar `configToYaml` en ControllerBrowser): la memoización ya vivía en el
call site (`useMemo`) y se mantuvo intacta. No se tocó.

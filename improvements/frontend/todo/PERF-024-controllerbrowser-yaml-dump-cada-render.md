---
id: PERF-024
title: ControllerBrowser recomputa originalYaml vía yaml.dump en cada render (y puede pisar ediciones)
category: performance
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/components/bots/ControllerBrowser.tsx:88
  - frontend/src/components/bots/ControllerBrowser.tsx:65
  - frontend/src/components/bots/ControllerBrowser.tsx:93-99
  - frontend/src/pages/tabs/ActiveBotsTab.tsx:789
commits: []
created: 2026-06-10
---

## Problema
En `YamlConfigEditor` (`ControllerBrowser.tsx:88`), `const originalYaml = configToYaml(config)`
corre en cada render. `configToYaml` (línea 65) filtra keys y llama `yamlLib.dump(filtered, { ..., sortKeys: true })`
— un dump+sort completo no trivial. El editor vive en un overlay fullscreen renderizado en
`ActiveBotsTab.tsx:789`, y esa tab re-renderiza en cada tick WS de bots produciendo un array
`controllers` fresco; `config={activeCtrl.config || {}}` se deriva vía `controllers.find(...)`,
así que la identidad del objeto `config` cambia cada tick → el YAML se re-dumpea en cada tick.
Peor: `isDirty = yamlContent !== originalYaml` (línea 99) compara contra el string recién dumpeado,
y el `useEffect` sobre `[config]` (93-97) llama `setYamlContent(configToYaml(config))` cada tick,
**pisando ediciones sin guardar** y reseteando el estado dirty cuando la identidad de `config` churnea.

## Solución propuesta
Memoizar con `useMemo(() => configToYaml(config), [config])` para que solo se re-dumpee cuando
`config` realmente cambia. La referencia estable matchea la dep del `useEffect` existente y es
seguro (no cambia el comportamiento en cambios reales de contenido). `isDirty` entonces compara
contra un valor memoizado estable.

## Criterio de aceptación
- [ ] `originalYaml` se computa dentro de un `useMemo` keyed sobre `config`
- [ ] `yamlLib.dump` no se llama en renders donde `config` no cambió (verificable con log/profiling)
- [ ] El botón Reset y el indicador dirty se comportan igual (y dejan de pisar ediciones por ticks WS)

## Notas
Relacionado con [[ARCH-034]] (centralizar `configToYaml` en `lib/`): conviene memoizar acá y, al
extraer el helper compartido, mantener la memoización.

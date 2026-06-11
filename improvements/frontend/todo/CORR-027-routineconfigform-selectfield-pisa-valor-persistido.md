---
id: CORR-027
title: RoutineConfigForm SelectField pisa un valor persistido válido que no está en la lista de opciones actual
category: correctness
impact: medium
effort: S
risk: low
status: todo
files:
  - frontend/src/components/routines/RoutineConfigForm.tsx:35-39
  - frontend/src/components/routines/RoutineConfigForm.tsx:107
commits: []
created: 2026-06-10
---

## Problema
En `SelectField`, el efecto en `RoutineConfigForm.tsx:35-39` fuerza `onChange(options[0])` cuando
el valor actual no está contenido en el array de opciones recién cargado:
```
if (options.length === 0) return;
if (value && value !== "" && options.includes(String(value))) return;
onChange(options[0]);
```
Si una routine fue configurada antes con un valor que el endpoint dinámico de opciones ya no
devuelve (ej. un server/pair temporalmente ausente, u opciones de otro server porque `options_from`
es server-scoped — el `queryKey`/`queryFn` en 26-27 incluyen `server`), el valor guardado del
usuario se **sobrescribe silenciosamente** con `options[0]`, mutando el config real. Además
`onChange` se pasa inline (`RoutineConfigForm.tsx:107`, `onChange={(v) => onChange(key, v)}`) y está
en el dep array (línea 39), así que el efecto re-corre en cada render del padre y el overwrite
puede re-dispararse al recargar opciones.

## Solución propuesta
Auto-seleccionar solo cuando genuinamente no hay valor (`value == null || value === ''`), y no
resetear un valor no vacío solo porque está ausente de las opciones actuales. Opcionalmente
renderizar el valor desconocido como un `<option>` deshabilitado de fallback para preservarlo y
hacerlo visible. Evitar depender del `onChange` inline inestable (envolver en `useCallback` en el
padre u omitirlo de las deps una vez que el trigger es la presencia de valor).

## Criterio de aceptación
- [ ] Un valor configurado ausente de la lista de opciones actual se preserva, no se reemplaza con `options[0]`
- [ ] La auto-selección de `options[0]` solo ocurre cuando no había valor previo
- [ ] El efecto deja de re-correr en cada re-render del padre por una nueva identidad de `onChange`

## Notas
Corrompe config persistida del usuario silenciosamente. Bug de datos, no cosmético.

---
id: ARCH-013
title: formatAgo idéntico copiado en Routines.tsx y Reports.tsx (y una tercera variante en RoutineInstances)
category: architecture
impact: low
effort: S
risk: low
status: done
files:
  - frontend/src/pages/Routines.tsx:361-367
  - frontend/src/pages/Reports.tsx:16-22
  - frontend/src/components/routines/RoutineInstances.tsx:20-27
  - frontend/src/lib/formatters.ts:46
commits: [aff33e5]
created: 2026-06-10
---

## Problema
`Routines.tsx:361-367` y `Reports.tsx:16-22` contienen `formatAgo(iso: string)` byte-a-byte
idéntico (lógica s/m/h/d ago sobre `Date.now() - new Date(iso).getTime()`).
`RoutineInstances.tsx:20-27` tiene una tercera variante casi igual que acepta epoch en segundos
(`ts: number`) con fallback "never". `lib/formatters.ts:46` ya es el hogar de helpers de tiempo
(`formatAge`), pero `formatAge` produce un formato distinto ("2d 3h", no "Nd ago"), así que esta
duplicación está genuinamente sin cubrir. Es propensa a divergir (las tres copias ya difieren en
tipo de input y fallback).

## Solución propuesta
Añadir un único `formatRelativeTime` en `lib/formatters.ts` que acepte epoch-segundos o un
Date/ISO (normalizando internamente), y reemplazar las tres definiciones locales. Mantener el
sufijo "ago"/"never" según el caso de `RoutineInstances` vía parámetro.

## Criterio de aceptación
- [x] `formatAgo` no se define localmente en `Reports.tsx` ni `Routines.tsx` ni `RoutineInstances.tsx`
- [x] Existe un único helper de tiempo relativo en `lib/formatters.ts` usado por los tres
- [x] Las etiquetas de "hace X" se muestran igual en Routines, Reports e instancias

## Notas
Misma familia de helpers de tiempo que [[READ-019]] (`tsToSeconds`); se pueden agrupar en un solo PR.

### Reconciliación (implementación)
Se añadió `formatRelativeTime(value, fallback = "")` en `lib/formatters.ts`. Acepta
epoch-segundos (`number`), o `Date`/ISO `string` (normaliza internamente; reusa `tsToSeconds`
para ms→s) y `null`/`undefined`/`""` → `fallback`. La lógica de buckets s/m/h/d es idéntica
byte-a-byte a las tres copias originales, por lo que la salida mostrada NO cambia:
- `Routines.tsx` y `Reports.tsx` pasaban ISO → `formatRelativeTime(iso)` (fallback `""`, nunca
  se alcanzaba con sus datos).
- `RoutineInstances.tsx` pasaba epoch-segundos con fallback `"never"` →
  `formatRelativeTime(inst.last_run_at, "never")`. Mejora menor sin impacto visible: el original
  usaba `if (!ts)` (trataba `0` como "never"); ahora solo `null/undefined` activan el fallback,
  irrelevante porque `last_run_at` nunca es `0`.

### Fuera de alcance
Existe una CUARTA copia: `formatAgo(iso)` exportada en `lib/routineUtils.ts:55`, ya compartida por
`RoutineReports.tsx` y `ReportBrowser.tsx`. El item solo lista las tres definiciones locales, así que
se dejó intacta para no expandir el scope. Candidata a un follow-up: reexportar/alias a
`formatRelativeTime` y migrar esos dos consumidores.

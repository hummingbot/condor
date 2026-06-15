---
id: CORR-029
title: parse-agent parseToolCalls usa un magic-number frágil para calcular los límites de bloque
category: correctness
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/lib/parse-agent.ts:340
  - frontend/src/lib/parse-agent.ts:346
commits:
  - "52988df (fix) limites de bloque exactos en parseToolCalls (CORR-029)"
created: 2026-06-10
---

## Problema
En `parseToolCalls`, cada header guarda su `index` como `match.index + match[0].length` (posición
DESPUÉS de la línea del header, `parse-agent.ts:340`). Para encontrar dónde termina el bloque `i`,
el código computa `headers[i+1].index - headers[i+1].name.length - 20` (línea 346) — una heurística
hecha a mano que intenta retroceder sobre el siguiente header `### N. name (status)` usando el
literal `20` más el largo del nombre. Pero el largo real del header es
`'### ' + N + '. ' + name + ' (' + status + ')'`, que varía con la cantidad de dígitos de N y el
largo del status, así que `20` ignora `status.length` y `digits(N)`. Ejemplo: para `### 1. swap (ok)`,
`match[0].length=16`, `name.length=4` → `blockEnd = (start+16) - 4 - 20 = start - 8`, cortando 8
chars antes de que empiece el header siguiente (comiéndose el `output` del bloque actual). Con
status largos o N multi-dígito puede pasarse y filtrar el header siguiente (su `**Input:**`) al
bloque actual, contaminando la extracción de Input/Output → se pierden detalles de tool-call en la
vista de snapshots del `SessionReviewer`.

## Solución propuesta
Guardar el offset de inicio de cada header por separado (ej. `start: match.index`) junto al
`index` post-match, y luego
`blockEnd = i + 1 < headers.length ? headers[i + 1].start : text.length`. Eliminar la aritmética
mágica `- name.length - 20` por completo. Esto da límites exactos sin números mágicos.

## Criterio de aceptación
- [x] Los bloques Input/Output se cortan usando el `match.index` real del header siguiente, no un offset hardcodeado
- [x] Tool calls con nombres cortos y N/status de tamaño variable parsean Input/Output correctamente
- [x] Ningún bloque de tool-call contiene fragmentos del header `### N. name (status)` siguiente
- [x] No queda el literal `- 20` en el cálculo de fin de bloque

## Notas
Hallazgo reportado tanto por la lente CORR como READ — es un bug de correctness en parsing puro.

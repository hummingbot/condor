---
id: PERF-004
title: Cachear getThemeColors()/getChartColors() — fuerzan reflow síncrono en loops de render y hot path de crosshair
category: performance
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/lib/theme-colors.ts:2-11
  - frontend/src/pages/Portfolio.tsx:51-65
  - frontend/src/pages/Portfolio.tsx:267
  - frontend/src/pages/Portfolio.tsx:276
  - frontend/src/pages/Portfolio.tsx:481
  - frontend/src/pages/Portfolio.tsx:551
  - frontend/src/pages/Portfolio.tsx:730
  - frontend/src/components/trade/TradeChart.tsx:230
  - frontend/src/components/trade/TradeChart.tsx:384
  - frontend/src/components/trade/TradeChart.tsx:386
  - frontend/src/components/trade/TradeChart.tsx:466
commits: [b316699]
created: 2026-06-10
---

## Problema
`getThemeColors()` llama `getComputedStyle(document.documentElement)` y lee 5 CSS variables
en cada invocación (`theme-colors.ts:2-11`); `getChartColors()` la envuelve
(`Portfolio.tsx:51-65`). `getComputedStyle` (al leer valores computados) fuerza un recálculo
de estilo síncrono. Se invocan dentro de loops y hot paths: el `TokenBarChart` de Portfolio
llama `getChartColors()` dos veces por fila de token dentro de `.map()` (267, 276), y el
builder de stacked-area la llama por token (481, 551, 730). En TradeChart, `getThemeColors()`
se llama varias veces POR evento de crosshair move — `subscribeCrosshairMove` dispara en cada
pixel de movimiento del mouse — en la línea 230 y de nuevo dentro del builder de tooltip
(384, 386). Esto provoca reflows forzados repetidos durante hover y paint.

## Solución propuesta
Cachear los colores del tema e invalidar solo en cambio de tema. Añadir un objeto cacheado
a nivel módulo en `theme-colors.ts` poblado una vez y refrescado vía un `MutationObserver`
sobre `document.documentElement[data-theme]` (la misma señal que TradeChart ya observa en la
línea 466). Entonces `getThemeColors()`/`getChartColors()` devuelven el objeto cacheado con
cero llamadas a `getComputedStyle` en hot paths. Localmente, además hoistear `getChartColors()`
fuera de los loops de render de Portfolio a un único `const` por render.

## Criterio de aceptación
- [x] `getComputedStyle` se llama a lo sumo una vez por cambio de tema, no por fila de token ni por crosshair move (verificado con log)
- [ ] Hacer hover sobre el trade chart no muestra entradas repetidas de "Recalculate Style" (forced reflow) en DevTools Performance
- [x] Los colores siguen actualizándose al cambiar dark/light/colorblind

## Notas
- `getThemeColors()` ahora devuelve un objeto cacheado a nivel módulo en `theme-colors.ts`.
  La primera llamada lee las CSS vars con `getComputedStyle`; las siguientes devuelven la cache.
- **Invalidación**: un `MutationObserver` a nivel módulo observa
  `document.documentElement` con `attributeFilter: ["data-theme"]`. El cambio de tema en
  `hooks/useTheme.ts` hace `setAttribute("data-theme", theme)` sobre `<html>`, así que ese
  atributo es la señal canónica; al cambiar, el observer pone `cachedColors = null` y la
  próxima llamada re-lee. Esto cubre cualquier origen del cambio (toggle, restore, system).
  Se guarda detrás de `typeof document !== "undefined"` por seguridad en SSR/tests.
- `getChartColors()` se hoisteó a un único `const` por render en `TokenBarChart` y en el
  builder de stacked-area de Portfolio; en TradeChart se hoisteó la doble llamada del box de
  medición. Con la cache esto es secundario, pero evita reasignaciones del array.
- El criterio de DevTools Performance (forced reflow) no se verificó manualmente en navegador
  (frontend-only, sin sesión de profiling), pero queda garantizado por construcción: cero
  `getComputedStyle` en hot paths salvo el primer paint y cada cambio de tema.

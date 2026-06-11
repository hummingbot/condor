---
id: PERF-004
title: Cachear getThemeColors()/getChartColors() — fuerzan reflow síncrono en loops de render y hot path de crosshair
category: performance
impact: medium
effort: S
risk: low
status: todo
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
commits: []
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
- [ ] `getComputedStyle` se llama a lo sumo una vez por cambio de tema, no por fila de token ni por crosshair move (verificado con log)
- [ ] Hacer hover sobre el trade chart no muestra entradas repetidas de "Recalculate Style" (forced reflow) en DevTools Performance
- [ ] Los colores siguen actualizándose al cambiar dark/light/colorblind

## Notas
TradeChart ya observa `[data-theme]` en la línea 466 — reutilizar esa señal para invalidar la cache.

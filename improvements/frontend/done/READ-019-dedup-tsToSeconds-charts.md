---
id: READ-019
title: tsToSeconds duplicado byte a byte en 3 archivos de charts
category: readability
impact: medium
effort: S
risk: low
status: done
files:
  - frontend/src/pages/tabs/BacktestingTab.tsx:27-29
  - frontend/src/components/charts/ArchivedPerformanceCharts.tsx:34-36
  - frontend/src/components/charts/ExecutorChart.tsx:49-51
commits:
  - f45f02c
created: 2026-06-10
---

## Problema
La función `tsToSeconds(ts) => ts > 1e12 ? Math.floor(ts/1000) : ts` está copiada idénticamente en
`BacktestingTab.tsx:27-29`, `ArchivedPerformanceCharts.tsx:34-36` y `ExecutorChart.tsx:49-51`. Es
lógica de normalización ms↔s usada en 20+ call sites a través de los 3 charts (overlays de cajas,
segmentos, series de PnL, candles); si la heurística del umbral (1e12) cambia, hay que recordar
editar 3 sitios y es fácil que diverjan silenciosamente.

## Solución propuesta
Mover `tsToSeconds` a un módulo compartido (`lib/formatters.ts` o un nuevo `lib/time.ts`),
exportarla y reemplazar las 3 definiciones locales por el import.

## Criterio de aceptación
- [x] `tsToSeconds` existe en un solo módulo exportado
- [x] Los 3 archivos de charts importan esa única definición y no tienen copia local
- [ ] Los charts renderizan los ejes temporales igual que antes

## Notas
Misma familia de helpers de tiempo que [[ARCH-011]] y [[ARCH-013]]; agrupables en un solo PR de DRY.

Implementado: `tsToSeconds` exportado desde `frontend/src/lib/formatters.ts` (junto a los otros helpers de tiempo); las 3 copias locales eliminadas y reemplazadas por import. El criterio de render visual no se verificó en navegador (sin entorno corriendo), pero el cambio es un refactor sin alterar la lógica (definición byte a byte idéntica), por lo que el comportamiento es equivalente. tsc -b exit 0; eslint sin nuevos errores (baseline: 1 error preexistente en BacktestingTab:512 no relacionado).

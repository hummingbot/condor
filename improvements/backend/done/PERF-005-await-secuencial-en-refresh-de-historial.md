---
id: PERF-005
title: await secuencial de 4 rangos de historial en el refresh loop del portfolio web
category: performance
impact: medium
effort: S
risk: low
status: done
files:
  - condor/web/routes/portfolio.py:214
  - condor/web/routes/portfolio.py:191
commits:
  - "a181dea (perf) paralelizar fetch de historial con asyncio.gather en refresh loop del portfolio web (PERF-005)"
created: 2026-06-10
---

## Problema
`_refresh_loop()` (líneas 214-222 de `condor/web/routes/portfolio.py`) hace fetch de 4 rangos de
historial (1D, 1W, 1M, 3M) de forma **secuencial** dentro de un `for` con `await` adentro. Cada
`_fetch_history()` espera a que termine el anterior, totalizando ~4x la latencia (≈4s en lugar de ≈1s).
El mismo módulo ya resuelve esto correctamente: `warm_portfolio_history()` (línea 191) usa
`asyncio.gather()` para las mismas llamadas independientes.

## Solución propuesta
Reemplazar el loop secuencial por `asyncio.gather()` sobre los 4 rangos, replicando el patrón ya
presente en `warm_portfolio_history()` (línea 191). Mantener el manejo de excepciones por rango
(`return_exceptions=True` o try/except dentro de cada fetch) para que un fallo no tumbe a los demás.

## Criterio de aceptación
- [x] Los 4 rangos se consultan en paralelo (`asyncio.gather`)
- [x] El ciclo de refresh baja de ~4s a ~1s
- [x] Un fallo en un rango no impide cachear los demás
- [x] No se rompe ningún test existente

## Notas
Las llamadas son independientes (no hay dependencia de datos entre rangos), por eso el riesgo es bajo.
Consistencia directa con el patrón ya usado en `warm_portfolio_history()`.

**Cierre (a181dea):** Resuelto: los 4 rangos se consultan con `asyncio.gather(..., return_exceptions=True)`, replicando `warm_portfolio_history()`. Un fallo por rango no tumba los demás.

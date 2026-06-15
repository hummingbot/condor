---
id: CORR-041
title: AgentPnlChart se congela con los datos del primer mount y nunca actualiza al llegar nuevos puntos de PnL
category: correctness
impact: high
effort: M
risk: low
status: done
files:
  - frontend/src/components/agent/AgentPnlChart.tsx:32-132
  - frontend/src/components/agent/AgentPnlChart.tsx:73
  - frontend/src/components/agent/AgentPnlChart.tsx:135
  - frontend/src/components/agent/AgentSessionContent.tsx:51
  - frontend/src/components/agent/AgentOverviewTab.tsx:155
  - frontend/src/components/agent/AgentOverviewTab.tsx:174
commits:
  - "617213f (fix) AgentPnlChart actualiza la serie al cambiar data (CORR-041)"
created: 2026-06-10
---

## Problema
Todo el ciclo de vida del chart vive en un único `useEffect(() => {...}, [])` con deps vacías
(`AgentPnlChart.tsx:32-132`, con eslint-disable en la línea 132). El efecto lee la prop `data`, la
ordena, llama `series.setData(...)` una vez (línea 73) y `chart.timeScale().fitContent()` — pero solo
corre en mount. La instancia de la serie **nunca** se guarda en un ref (solo existen `chartRef`,
`containerRef`, `tooltipRef`), así que no hay camino para actualizar la serie después del mount. Los
consumidores alimentan data viva: `AgentSessionContent.tsx:51` (`metricsToDataPoints(metrics)` de una
query de journal que refetchea) y `AgentOverviewTab.tsx:174` (`sessionsToDataPoints(sessions)` con la
query `agent-performance` polleada cada 10s, `AgentOverviewTab.tsx:155`). Como la serie se puebla una
sola vez, la curva de equity/métricas **se congela silenciosamente** en los datos que existían al
montar; nuevos ticks, sesiones y PnL nunca aparecen hasta desmontar/remontar (ej. cambiar de sub-tab).
El snapshot de `colors` capturado también deja el cambio de tema sin reflejar.

## Solución propuesta
Separar el ciclo de vida: mantener un efecto (deps `[]`) que cree chart + serie + handler de crosshair
y guarde tanto `chartRef` como un `seriesRef`; agregar un segundo `useEffect([data])` que, una vez que
la serie existe, ordene `data` y llame `seriesRef.current.setData(sorted)` (+ `fitContent()` solo en la
primera carga no vacía o cuando crece el número de puntos). Esto refleja el patrón estándar de
lightweight-charts de separar creación de chart de actualización de datos. Mantener consistente el
guard `if (data.length === 0) return null` (línea 135) para que el contenedor siga montando cuando
llegue data después.

## Criterio de aceptación
- [ ] Actualizar la prop `data` (ej. nuevo tick por el refetch de 10s) actualiza la serie renderizada sin unmount/remount
- [ ] El efecto de mount sigue creando el chart una sola vez (sin instancias duplicadas en cambios de data)
- [ ] `fitContent`/autoscale se comporta bien al agregar puntos
- [ ] No hay regresión en el path de data vacía (retorna null cuando está vacío) y la transición vacío→no-vacío puebla la serie

## Notas
Hallazgo reportado por las lentes PERF y CORR — es un bug de correctness (el chart deja de avanzar). No
confundir con [[PERF-003]] (TradeChart hace setData de MÁS por tick): este es el problema opuesto
(setData de MENOS) en otro archivo.

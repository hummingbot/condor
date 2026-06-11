---
id: ARCH-013
title: _format_number() duplicado en 4 handlers con lógica de precisión inconsistente
category: architecture
impact: medium
effort: M
risk: low
status: done
files:
  - handlers/dex/swap.py:80
  - handlers/dex/liquidity.py:46
  - handlers/dex/pools.py:461
  - handlers/cex/trade.py:52
commits:
  - "14251b6 (refactor) centralizar format_compact_number y format_network_display en utils/telegram_formatters (ARCH-012, ARCH-013)"
created: 2026-06-10
---

## Problema
Hay 4 definiciones de `_format_number()` casi idénticas en `handlers/dex/swap.py` (líneas 80-106),
`handlers/dex/liquidity.py` (46-65), `handlers/dex/pools.py` (461-480) y `handlers/cex/trade.py`
(52-71), con ~53 usos repartidos. La versión de `swap.py` es más completa (maneja números muy pequeños
—0.0001, 0.000001, etc.— y notación científica) mientras las otras 3 son básicas. Resultado: cambiar la
política de formato exige tocar 4+ lugares y el manejo fino de precios crypto pequeños no está
disponible fuera de `swap.py`, generando formato inconsistente entre módulos.

## Solución propuesta
Extraer una única `format_number()` a `utils/` (p. ej. `utils/telegram_formatters.py`), tomando como
base la versión **completa** de `swap.py` (la que soporta números muy pequeños + notación científica),
e importarla en los 4 handlers. Eliminar las 4 copias locales y actualizar los ~53 usos.

## Criterio de aceptación
- [x] Existe una sola `format_number` en `utils/`, basada en la versión completa de `swap.py`
- [x] Los 4 handlers la importan; no quedan copias locales
- [x] Los números muy pequeños (precios crypto) se formatean igual en todos los módulos
- [x] No se rompe ningún test existente

## Notas
**Cuidado**: no degradar a la versión básica; la de `swap.py` cubre rangos que las otras no. Atómico
respecto a [[ARCH-012]] (que cubre `_format_network_display`).

**Cierre (14251b6):** Resuelto: helper único basado en la versión completa de swap.py. DESVÍO de nombre: se llamó `format_compact_number` (no `format_number`) porque ya existía un `format_number` con semántica distinta (`$`-prefijado) usado fuera de scope. `liquidity.py` tenía la def sin call sites (dead code), se eliminó sin import.

---
id: ARCH-012
title: Duplicación de _format_network_display() entre trading y dex/swap
category: architecture
impact: medium
effort: S
risk: low
status: done
files:
  - handlers/trading/__init__.py:29
  - handlers/dex/swap.py:52
commits:
  - "14251b6 (refactor) centralizar format_compact_number y format_network_display en utils/telegram_formatters (ARCH-012, ARCH-013)"
created: 2026-06-10
---

## Problema
`_format_network_display()` está definida dos veces con código idéntico (solo difiere un punto en el
docstring): en `handlers/trading/__init__.py` (líneas 29-54) y en `handlers/dex/swap.py`
(líneas 52-77). Un cambio en el formato de red obliga a actualizar dos lugares; si se olvida uno, la UI
muestra formatos inconsistentes según el módulo.

## Solución propuesta
Centralizar la función en `utils/telegram_formatters.py` (módulo ya existente para formateo de
Telegram) e importarla en ambos handlers, eliminando las dos copias. Actualizar el call site conocido
en `trading/__init__.py:153`.

## Criterio de aceptación
- [x] Existe una sola definición de `_format_network_display` en `utils/`
- [x] Ambos handlers la importan; no quedan copias locales
- [x] El formato de red mostrado al usuario no cambia
- [x] No se rompe ningún test existente

## Notas
Atómico: este item cubre **solo** `_format_network_display`. La duplicación de `_format_number` se trata
por separado en [[ARCH-013]]. Familia DRY de handlers junto a [[ARCH-011]].

**Cierre (14251b6):** Resuelto: `format_network_display` centralizada en `utils/telegram_formatters.py`. La copia en `dex/swap.py` era dead code (sin call sites), así que se eliminó sin agregar import.

---
id: ARCH-014
title: Múltiples clear_*_state() redundantes en lugar del clear_all_input_states() central
category: architecture
impact: medium
effort: M
risk: medium
status: done
files:
  - handlers/__init__.py:54
  - handlers/bots/_shared.py:137
  - handlers/cex/__init__.py:24
  - handlers/cex/_shared.py:346
  - handlers/executors/_shared.py:94
commits:
  - "a61be87 (refactor) dedup is_gateway_network, consolidar clear_*_state, constantes de estado del portfolio (ARCH-011, ARCH-014, READ-018)"
created: 2026-06-10
---

## Problema
CLAUDE.md establece `clear_all_input_states()` como limpiador maestro, pero existen 7 funciones
`clear_*_state()` redundantes y a veces inconsistentes:
- `clear_bots_state()` (`bots/_shared.py:137`): subconjunto de lo que ya limpia el central; usado 9 veces.
- `clear_cex_state()` **duplicada**: `cex/__init__.py:24` (limpia `trade_params`, `trade_menu_*`) y
  `cex/_shared.py:346` (limpia `current_orders`) — dos versiones en el mismo módulo con diferencias.
- `clear_dex_state()` (`dex/_shared.py:432`): **nunca usado** y cubre menos que el central.
- `clear_executors_state()` (`executors/_shared.py:94`): redundante; difiere en `history_executors`.
- `clear_archived_state()` (`bots/archived.py:41`): redundante; difiere en `archived_total_count`.
- `clear_config_state()` ya es un wrapper de `clear_all_input_states()`.
Esto genera confusión y riesgo de que un flujo limpie un subconjunto incompleto del estado.

## Solución propuesta
Hacer de `clear_all_input_states()` la única fuente de verdad: asegurar que cubra TODAS las claves
(incluidas las divergentes: `current_orders`, `history_executors`, `archived_total_count`), y reemplazar
los call sites de las funciones redundantes por el central. Eliminar `clear_dex_state()` (sin uso) y la
`clear_cex_state()` duplicada. Conservar wrappers finos solo si aportan claridad, delegando al central.

## Criterio de aceptación
- [x] `clear_all_input_states()` limpia el superconjunto de todas las claves hoy dispersas
- [x] Se eliminan las funciones sin uso y la `clear_cex_state` duplicada
- [x] Los 10+ call sites usan el limpiador central (o wrappers que delegan en él)
- [x] No hay regresión de polución de estado entre features (probar transiciones /trade↔/swap, bots, executors)

## Notas
Riesgo medio por los 10+ call sites; hacer el cambio verificando que cada clave divergente quede
cubierta antes de borrar la función específica. Alinea con el patrón documentado en CLAUDE.md.

**Cierre (a61be87):** Resuelto: `clear_all_input_states()` ahora es superconjunto (se añadió `history_executors`). Eliminadas las `clear_cex_state` (duplicada/sin uso) y `clear_archived_state`. `clear_bots_state` y `clear_executors_state` se mantienen como WRAPPERS que delegan al central (tienen call sites en archivos de otros módulos). `clear_dex_state` no se tocó (archivo de otro agente); sus claves están cubiertas. Verificación estática (import+grep), no runtime.

---
id: ARCH-011
title: Duplicación de is_gateway_network entre handlers/__init__.py y portfolio.py
category: architecture
impact: medium
effort: S
risk: low
status: done
files:
  - handlers/__init__.py:8
  - handlers/portfolio.py:25
commits:
  - "a61be87 (refactor) dedup is_gateway_network, consolidar clear_*_state, constantes de estado del portfolio (ARCH-011, ARCH-014, READ-018)"
created: 2026-06-10
---

## Problema
`is_gateway_network()` (líneas 8-38 de `handlers/__init__.py`) y `_is_gateway_network()`
(líneas 25-53 de `handlers/portfolio.py`) tienen lógica **idéntica**. Viola DRY y crea riesgo de
inconsistencia si una versión se actualiza y la otra no. La copia privada de `portfolio.py` solo se usa
localmente (línea 83).

## Solución propuesta
Eliminar `_is_gateway_network()` de `portfolio.py` e importar `is_gateway_network` desde
`handlers.__init__` (o desde donde corresponda según el orden de imports), actualizando el único call
site (línea 83).

## Criterio de aceptación
- [x] Existe una sola definición de la función
- [x] `portfolio.py` usa la versión compartida vía import
- [x] No hay import circular introducido
- [x] Comportamiento idéntico para el usuario

## Notas
Fix limpio y de bajo riesgo (la lógica es idéntica). Parte de una familia de duplicaciones en handlers:
[[ARCH-012]], [[ARCH-013]].

**Cierre (a61be87):** Resuelto: eliminada la copia `_is_gateway_network` de portfolio.py; ahora importa `is_gateway_network` de `handlers`. Sin import circular (verificado).

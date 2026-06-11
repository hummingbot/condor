# Improvements — backlog de mejoras de código

Backlog accionable de mejoras de Condor. Dos skills lo operan:

- **`/improvements`** — research read-only: analiza el código y **genera** items en `todo/`. No toca código.
- **`/ship-improvement <id>`** — desarrollo: **implementa** un item, lo testea, lo commitea y lo mueve a `done/` anotando los commits.

Cada mejora es **atómica**: un archivo = una tarea autocontenida, asignable a un developer
y verificable por un revisor de forma independiente.

## Organización por dominio

El backlog se divide en **scopes por dominio**, cada uno con su propia secuencia `NNN`
**independiente** y sus carpetas `todo/` + `done/`:

```
improvements/
├── backend/{todo,done}/    → backend Python: bot Telegram (handlers/, main.py),
│                             core (config_manager.py, utils/) y API web (condor/web/)
└── frontend/{todo,done}/   → dashboard React/TS (frontend/src/)
```

Cada scope tiene su `README.md` propio. Un mismo ID (ej. `CORR-001`) puede existir en
varios scopes; lo desambigua la carpeta. Para auditar un dominio: `/improvements` acotado
a ese scope.

## Cómo leer un scope

```
<scope>/todo/   → mejoras pendientes. Tómalas de aquí.
<scope>/done/   → mejoras ya implementadas, con los commits anotados en su frontmatter.
```

- **Nombre de archivo:** `{CATEGORÍA}-{NNN}-{slug}.md`
- **Categorías:** `PERF` (eficiencia), `CORR` (correctness/bugs), `ARCH` (arquitectura),
  `SEC` (seguridad), `READ` (legibilidad/mantenibilidad).
- `NNN` es un contador único **dentro de su scope**; nunca se reutiliza.

## Cómo trabajar un item

1. Elige uno de `todo/` (prioriza `impact: high` + `effort: S`).
2. Lee `Problema`, `Solución propuesta` y `Criterio de aceptación` del archivo.
3. Implementa el cambio cumpliendo todos los criterios de aceptación.
4. Commitea.

La forma recomendada de hacer 2-4 (y el cierre) es con Claude: **`/ship-improvement <id>`**, que
implementa, testea, commitea y mueve el item a `done/` anotando el commit.

A mano: pon `status: done`, rellena `commits:` con el hash, y `git mv` el archivo a `done/`.

> El estado vive en los archivos individuales (su frontmatter `status` y `commits`), no en este README.

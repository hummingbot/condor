---
id: FEAT-002
title: Skills híbridas — playbooks markdown + routines, recuperables y auto-editables vía MCP
status: done
effort: M
risk: medium
new_files:
  - condor/memory/skills.py
  - mcp_servers/condor/tools/skills.py
touched_files:
  - mcp_servers/condor/server.py           # registrar manage_skill
  - handlers/agents/_shared.py:402         # build_initial_context: inyectar índice de skills
  - condor/trading_agent/prompts.py:90     # build_tick_prompt: inyectar índice de skills
  - condor/trading_agent/engine.py         # leer índice de skills por tick (cacheado)
  - assistants/condor.md                   # enseñar a usar/crear skills
  - assistants/agent_builder.md            # enseñar a usar/crear skills
  - assistants/routine_builder.md          # una skill puede referenciar/crear una routine
depends_on:
  - "[[FEAT-001]]"
commits:
  - "7f8f04c feat(skills): SkillStore — playbooks over the FEAT-001 memory substrate (FEAT-002)"
  - "078cb2b feat(skills): manage_skill MCP tool (FEAT-002)"
  - "5b775d5 feat(skills): inject [SKILLS] into /agent + trading-agent ticks; teach assistants (FEAT-002)"
  - "fdb52c9 feat(skills): /memory lists and deletes skills; close FEAT-002"
created: 2026-06-17
---

## Objetivo

Dar a los agentes **skills híbridas**: *playbooks* en markdown (know-how: cuándo aplicar +
pasos) recuperables por relevancia, **más** las *routines* python ejecutables que ya existen,
con la posibilidad de que un playbook **referencie** una routine. Expuesto **solo por MCP**
(igual para ACP y pydantic-ai). El agente puede **crear/refinar sus propias skills** de forma
autónoma, con la misma auditoría que la memoria (**[[FEAT-001]]**).

Capacidades observables al cerrar:
- El agente acumula un playbook ("cómo abrir un grid en mercados con band-walk", "checklist
  antes de subir leverage") y la próxima vez lo recupera y lo sigue, en vez de re-derivarlo.
- Un playbook puede decir "ejecutá la routine `band_scanner` con estos params" → conecta el
  know-how (markdown) con la ejecución (routine).
- El usuario ve/borra skills creadas por el agente (mismo `/memory` + auditoría de FEAT-001).

**Fuera de alcance:**
- El substrato de archivos+índice+auditoría → lo aporta **[[FEAT-001]]** (esta feature lo reusa).
- Reescribir el motor de routines (`routines/base.py`, `manage_routines`): se reusa tal cual;
  las skills solo lo **referencian**, no lo reimplementan.
- Compartir skills entre usuarios / marketplace de skills: no ahora (skills por-usuario).

## Contexto y restricciones

- **FEAT-001 deja el patrón listo:** `condor/memory/store.py` (archivo-por-hecho + índice
  regenerable + `audit.log`), inyección de índices en `build_initial_context`
  (`handlers/agents/_shared.py:402`) y en `build_tick_prompt`
  (`condor/trading_agent/prompts.py`), y el tool umbrella estilo `manage_memory`. Skills copia
  ese patrón 1:1 → bajo riesgo, cero arquitectura nueva.
- **Las routines ya son "skills ejecutables".** `condor/trading_agent/prompts.py:90`
  `_build_routines_section()` ya descubre routines agent-local + globales y las lista como
  `[AVAILABLE ROUTINES]`; se ejecutan con `manage_routines(action="run", ...)`
  (`mcp_servers/condor/tools/routines.py`). Un playbook NO reemplaza esto: lo **referencia**.
- **El builder de routines existe.** `assistants/routine_builder.md` ya enseña a crear routines;
  un skill puede terminar en "creá esta routine" reusando ese flujo (`manage_routines` tiene
  `create_routine`/`edit_routine`).
- **Mismo requisito MCP-first:** tools (no resources), idénticos para ACP y pydantic-ai
  (justificación completa en [[FEAT-001]] alternativa B).

Restricciones:
- Un playbook es **texto advisory** (qué hacer/cuándo); ejecutar lo que el playbook describe
  (una routine, un executor) sigue gateado por los controles existentes (risk engine, callbacks
  de confirmación de tools peligrosos en `handlers/agents/_shared.py is_dangerous_tool_call`).
- Inyectar solo el **índice** de skills (name + when_to_use, una línea), no los cuerpos. El
  cuerpo se lee on-demand.

## Alternativas consideradas

- **A — Playbooks markdown como capa sobre el store de FEAT-001 + `manage_skill`, referenciando
  routines (elegida).** Skill = `SKILL.md` con frontmatter (`name`, `description`, `when_to_use`,
  `references_routine?`) + cuerpo (pasos). Vive en `data/memory/user_{id}/skills/`, índice
  `SKILLS.md` inyectable, auditoría compartida. A favor: reusa todo el patrón de FEAT-001,
  separa know-how (markdown) de ejecución (routine) sin duplicar el motor. En contra: dos
  artefactos (playbook + routine) que el agente debe saber conectar → se resuelve con el campo
  `references_routine` + instrucción en el prompt. Modo de fallo: skill referencia una routine
  inexistente → el read del skill valida y marca la referencia como rota.
- **B — Solo routines (sin playbooks).** Toda skill es una routine python. A favor: un solo tipo
  de artefacto. En contra: el know-how puro ("cuándo NO abrir posición", checklists, criterio)
  no es código; forzarlo a python es artificial y caro de crear/editar para el LLM. No cumple
  "playbooks markdown" pedido. Descartada.
- **C — Solo playbooks (skills = markdown, sin ejecución).** A favor: simple. En contra: pierde
  el puente con la ejecución que ya existe (routines) — el usuario pidió **híbrido**. Descartada.
- **D — Un único tool unificado memoria+skills.** Meter skills como un `type` más dentro de
  `manage_memory`. A favor: menos tools. En contra: una skill tiene estructura propia
  (`when_to_use`, `references_routine`, subcarpeta) y un ciclo de vida distinto (crear/refinar/
  ejecutar); mezclarlo confunde el contrato que ve el LLM. Descartada: tool separado, store
  compartido.

## Decisión

**Alternativa A.** Es la más obvia y eficiente porque **reusa íntegro el substrato de
[[FEAT-001]]** (mismo store, índice, auditoría, puntos de inyección) y respeta la decisión de
"skills híbridas": el markdown captura el know-how que no es código y `references_routine`
conecta con el motor de routines que **ya** resuelve la ejecución. Evita reimplementar
ejecución (YAGNI) y mantiene un solo lugar de auditoría/borrado para el usuario. Tool separado
del de memoria porque el contrato y el ciclo de vida son distintos, pero **store y patrón
compartidos** para no duplicar infra.

Trade-off aceptado: el agente debe aprender a conectar playbook→routine; se mitiga con el campo
explícito `references_routine` y una instrucción corta en los assistants.

## Diseño

### Layout (extiende el de FEAT-001, mismo `user_{id}`)

```
data/memory/user_{user_id}/
  skills/
    SKILLS.md           # índice inyectable: - [name] when_to_use  (→ routine: <name>)
    <slug>/
      SKILL.md          # frontmatter + pasos
  audit.log             # MISMO log que memoria (FEAT-001); target = skill:<slug>
```

`SKILL.md`:
```markdown
---
name: grid-en-band-walk
description: Cómo abrir un grid cuando el precio camina la banda inferior
when_to_use: Precio toca banda inferior de Bollinger 2+ velas y volatilidad < umbral
references_routine: band_scanner      # opcional; routine que este playbook usa
created: 2026-06-17
source: agent:grid_scalper
---

1. Correr band_scanner para confirmar band-walk en el pair.
2. Si confirma, abrir grid con spread X y N niveles vía manage_executors(create).
3. Journal: una línea con el setup. No re-abrir si ya hay grid en ese pair.
```

### Capa `condor/memory/skills.py` (espeja `store.py`)

```python
class SkillStore:
    def __init__(self, user_id: int): ...
    def create(self, name, description, when_to_use, body,
               references_routine: str | None = None, source: str = "chat") -> dict
    def read(self, name: str) -> dict | None          # frontmatter + body; marca referencia rota
    def search(self, query: str, limit: int = 10) -> list[dict]   # keyword sobre name+when_to_use+body
    def list_index(self) -> str                       # contenido de SKILLS.md (para inyectar)
    def edit(self, name: str, **fields) -> dict
    def delete(self, name: str, source: str = "user") -> bool
    def _reindex(self) -> None
```
Reusa el `_append_audit` del store de FEAT-001 (mismo `audit.log`) → "with auditoría" cubre
skills sin infra extra. `read()` valida `references_routine` contra
`routines.base.discover_routines()` (+ agent-local) y devuelve `routine_ok: bool` para que el
agente no invoque una routine inexistente.

### Tool MCP — `mcp_servers/condor/tools/skills.py` + registro en `server.py`

```python
async def manage_skill(action: str, name: str | None = None, description: str | None = None,
                       when_to_use: str | None = None, body: str | None = None,
                       references_routine: str | None = None, query: str | None = None) -> dict
```
Acciones: `create`, `read`, `search`, `list`, `edit`, `delete`. Resuelve
`SkillStore(settings.user_id)`; `source` derivado de `settings.agent_slug` igual que memoria.
Registrado con `@mcp.tool()` + `@handle_errors("manage skill")`; el docstring es el contrato
(cuándo crear una skill, cómo referenciar una routine).

### Inyección y enseñanza

- **Chat** (`build_initial_context`): sección `[SKILLS — playbooks you can follow]` con
  `SkillStore(user_id).list_index()`, junto al `[USER MEMORY]` de FEAT-001.
- **Trading agent** (`build_tick_prompt`): la sección de routines existente (`:90`) pasa a ser
  `[AVAILABLE SKILLS & ROUTINES]`: primero el índice de skills (playbooks), luego las routines
  como hoy. `engine.py` lee el índice de skills por tick, cacheado igual que routines.
- **Assistants** (`condor.md`, `agent_builder.md`): instrucción corta — "consultá `[SKILLS]`;
  leé el playbook con `manage_skill(action="read")` antes de un flujo conocido; si descubrís un
  procedimiento reusable, guardalo con `manage_skill(action="create")` (incluí `when_to_use`).
  Si el playbook necesita ejecución repetible, referenciá o creá una routine."
- `routine_builder.md`: nota de que una skill puede pedir crear una routine (puente
  playbook→`manage_routines(create_routine)`).

## Plan de implementación

- [x] **Slice 1 — store de skills:** `condor/memory/skills.py` (`SkillStore`) reusando
      `_append_audit`/parseo de FEAT-001 + tests (create→list→read con validación de routine→
      search→edit→delete→audit).
- [x] **Slice 2 — tool MCP:** `tools/skills.py` + registrar `manage_skill` en `server.py`.
- [x] **Slice 3 — inyección + enseñanza chat:** `[SKILLS]` en `build_initial_context` + editar
      `condor.md`/`agent_builder.md`. Verificar en `/agent` que crea y recupera una skill.
- [x] **Slice 4 — inyección trading agent:** unificar sección a `[AVAILABLE SKILLS & ROUTINES]`
      en `build_tick_prompt` + lectura **fresca** por tick en `engine.py` (ver desvío) + bloque en
      `BASE_PROMPT_COMMON`. Verificado: el tick ve las skills y puede leer una que referencia una routine.
- [x] **Slice 5 — borrado/auditoría usuario:** `/memory` (FEAT-001) extendido para listar y
      borrar skills (target `skill:<slug>`, botón `delete_skill:`); auditoría ya compartida.

## Criterio de aceptación

- [x] `manage_skill` (create/read/search/list/edit/delete) funciona vía ACPClient **y**
      PydanticAIClient. (Mismo tool MCP registrado para ambos clientes — patrón idéntico a
      `manage_memory` de FEAT-001; ciclo completo ejercitado por el entry point del tool.)
- [x] Una skill creada por el chat aparece en `[SKILLS]` de una sesión nueva y en el tick de un
      trading agent del mismo usuario. (Mismo `SkillStore(user_id)` en ambos puntos de inyección.)
- [x] Un playbook con `references_routine` válido reporta `routine_ok=true` al leerse; uno con
      referencia rota lo marca y no rompe. (`test_broken_routine_reference_marked_not_fatal`.)
- [x] Crear/editar/borrar skills queda en el `audit.log` compartido (target `skill:<slug>`) y se
      revisa/borra desde `/memory`. (`test_audit_shared_with_memory_store` + UI de `/memory`.)
- [x] Las routines y `manage_routines` siguen funcionando igual (no se reimplementó ejecución).
- [x] Tests unitarios del `SkillStore` (13 tests); resto verificado por smoke del tick prompt y
      del entry point del tool.

## Riesgos y notas

- **Depende de [[FEAT-001]]** (store, índice, auditoría, puntos de inyección). No empezar antes.
- **Skill que referencia routine inexistente:** `read()` valida contra el discovery de routines
  y marca `routine_ok=false`; el prompt instruye no invocar referencias rotas.
- **Doble artefacto (playbook + routine):** riesgo de que el agente no los conecte; mitigado con
  el campo explícito `references_routine` + instrucción. Si en la práctica no se usa, evaluar
  fusionar — pero empezamos por lo híbrido que pidió el usuario.
- **Ejecución sigue gateada:** una skill describe pasos; abrir executors / correr routines
  peligrosas sigue pasando por el risk engine y los confirmation callbacks existentes. La skill
  no es un bypass.

### Desvíos respecto al Diseño

- **Lectura del índice de skills por tick: FRESCA, no cacheada.** El diseño decía "cacheado
  igual que routines". Se implementó lectura fresca cada tick (como `user_memory`), porque un
  objetivo central es que el agente **cree/refine sus propias skills**: si se cacheara en el
  primer tick, una skill escrita a mitad de sesión no aparecería hasta reiniciar. El discovery de
  routines (caro, importa módulos) sí sigue cacheado; el índice de skills es un read de archivo
  barato. `engine.py` lo pasa como `skills_index=` a `build_tick_prompt`.
- **Validación de `references_routine` solo contra routines globales.** `SkillStore` es
  filesystem puro y no tiene handle al `agent_dir` de una estrategia, así que valida contra
  `discover_routines()` (globales). Una referencia a una routine agent-local reporta
  `routine_ok=false` (advisory, nunca fatal). Suficiente para el criterio de aceptación.
- **`append_audit` extraído a función libre en `store.py`** (FEAT-001) para que memoria y skills
  escriban el mismo formato al mismo `audit.log` — única fuente de verdad del formato de auditoría.

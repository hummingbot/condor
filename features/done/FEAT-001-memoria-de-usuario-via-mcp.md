---
id: FEAT-001
title: Memoria de usuario compartida (chat + trading agent) vía MCP, con auditoría
status: done
effort: L
risk: medium
new_files:
  - condor/memory/__init__.py
  - condor/memory/store.py
  - mcp_servers/condor/tools/memory.py
  - handlers/memory/__init__.py
  - scripts/migrate_notes_to_memory.py
touched_files:
  - mcp_servers/condor/server.py:175      # registrar manage_memory; deprecar manage_notes
  - mcp_servers/condor/tools/notes.py      # convertir en alias deprecado -> store
  - handlers/agents/_shared.py:402         # build_initial_context: inyectar índice de memoria
  - condor/trading_agent/prompts.py:118    # build_tick_prompt: inyectar índice de memoria
  - condor/trading_agent/engine.py         # leer índice de memoria del owner por tick (cacheado)
  - assistants/condor.md                   # enseñar a usar la memoria
  - assistants/agent_builder.md            # enseñar a usar la memoria
  - main.py                                # registrar handler /memory
depends_on: []
commits:
  - "cdada49 feat(memory): user memory store — file-per-fact + injectable index (FEAT-001)"
  - "b73cc87 feat(memory): manage_memory MCP tool; deprecate manage_notes as alias (FEAT-001)"
  - "24ee898 feat(memory): inject [USER MEMORY] into /agent context; teach assistants (FEAT-001)"
  - "c52a3c8 feat(memory): inject [USER MEMORY] into trading agent ticks (FEAT-001)"
  - "0ef5cda feat(memory): /memory command to review and prune user memory (FEAT-001)"
  - "104b19f feat(memory): migrate legacy data/notes/*.json into user memory (FEAT-001)"
created: 2026-06-17
---

## Objetivo

Dar al sistema una **memoria de usuario persistente y compartida** entre el chat `/agent`
(`handlers/agents/`) y los trading agents (`condor/trading_agent/`), expuesta **solo por MCP**,
para que ambos backends —ACPClient (plan) y PydanticAIClient (API/local)— la hereden sin
código por-cliente. El agente aprende del usuario de forma autónoma (estilo Hermes
self-editing) y todo lo que escribe/edita/borra queda **auditado y es revisable/borrable**
por el usuario.

Capacidades observables al cerrar:
- El usuario le dice algo al chat ("siempre reportá en USD", "mi exchange default es Binance")
  y eso persiste; en una sesión nueva —o en un tick de un trading agent suyo— el agente lo
  recuerda sin que se lo repita.
- El agente guarda hechos/aprendizajes por su cuenta; el usuario puede ver el log de qué
  guardó y borrar lo que no quiera (`/memory` en Telegram, o vía MCP).
- Lo que aprende el chat sobre el usuario lo ve el trading agent y viceversa (memoria de
  **usuario**, no de chat ni de agente).

**Fuera de alcance** (cubierto por otras features o explícitamente no ahora):
- Skills/playbooks → **[[FEAT-002]]** (esta feature es el substrato que FEAT-002 reusa).
- El journal operativo del trading agent (Ticks/Executors/Snapshots/learnings) **NO se toca**:
  sigue siendo memoria operativa por-agente. Decisión del usuario: operativo separado.
- Memoria con scope "chat" (efímera por conversación): no ahora. Solo scope **user**.
- Retrieval por embeddings: no (ver Decisión).

## Contexto y restricciones

Lo que ya existe y condiciona el diseño:

- **MCP es el bus común.** `mcp_servers/condor/server.py` registra tools con
  `@mcp.tool()` + `@handle_errors(...)`; las implementaciones viven en `tools/*.py`. Ambos
  backends consumen estos tools (PydanticAIClient vía `MCPServerStdio` toolsets; ACPClient
  vía config en `initialize`). Cualquier tool nuevo aquí lo ven los dos **gratis**.
- **El user_id ya está resuelto en el proceso MCP.** `mcp_servers/condor/settings.py`
  expone `settings.user_id` (de `--user-id` / `CONDOR_USER_ID`). El subproceso MCP se
  spawnea con `--user-id` tanto para el chat como para el trading agent
  (`handlers/agents/_shared.py` `_condor_mcp_args`). → la memoria puede keyear por `user_id`
  sin tocar el wire.
- **Patrón de "notas" existente pero muerto.** `tools/notes.py` guarda key-value JSON en
  `data/notes/chat_{chat_id}.json`. Está keyado por **chat**, no por usuario, y los
  assistants no lo usan (`assistants/condor.md:30` solo lo lista). Hay 2 archivos reales
  con un único tipo de nota (`server.*.group_chat_id`). Migración chica.
- **Inyección de contexto del chat.** `handlers/agents/_shared.py:402` `build_initial_context()`
  arma el system prompt (`_build_system_prompt` desde `assistants/*.md`) + info de servidor,
  y ya recibe `user_id`. Es el punto natural para inyectar el índice de memoria.
- **Inyección de contexto del trading agent.** `condor/trading_agent/prompts.py:118`
  `build_tick_prompt()` ya inyecta `learnings`, `summary`, `recent_decisions` como secciones
  `[...]`. `engine.py` los lee del journal cada tick y cachea la sección de routines. Mismo
  patrón para una sección `[USER MEMORY]`.
- **Modelo de referencia (decisión del usuario): Claude Code / Hermes.** Archivo-por-hecho
  con frontmatter (`name`, `description`, `type`) + un índice (`MEMORY.md`) que se inyecta en
  contexto; memoria auto-editable. Es exactamente el patrón de la memoria de este repo en
  `~/.claude/.../memory/`.
- **Regla de backend (memoria del proyecto `claude-auth-paths-acp-vs-oauth`):** Claude plan →
  ACP; metered/local → pydantic-ai. La capa de memoria **no debe acoplarse a un backend** —
  por eso vive 100% en MCP + en los builders de prompt comunes.

Restricciones:
- La memoria es **contexto advisory**, nunca ejecutable. No dispara acciones por sí sola.
- Por-tick el costo de tokens importa: inyectar el **índice** (una línea por memoria), no los
  cuerpos completos. El agente lee el cuerpo on-demand vía tool.
- Escrituras concurrentes posibles (chat + varios ticks del mismo usuario). El store debe ser
  seguro a nivel archivo (write atómico + relectura del índice antes de reescribir).

## Alternativas consideradas

- **A — Tools MCP sobre archivos markdown con índice (elegida).** `manage_memory` (write/
  read/search/list/delete/audit) sobre `data/memory/user_{id}/`, un archivo por hecho con
  frontmatter + `MEMORY.md` como índice inyectable. A favor: idéntico al patrón de referencia
  pedido, legible/auditables a mano, retrieval barato (índice chico), funciona igual en ACP y
  pydantic-ai. En contra: retrieval por keyword, no semántico. Modo de fallo: índice y
  archivos se desincronizan → mitigado regenerando el índice desde los archivos en cada
  escritura.
- **B — MCP *resources* en vez de tools.** Exponer memorias como `resources` MCP. A favor:
  semánticamente "datos, no acciones". En contra: el soporte de `resources` es **disparejo**
  entre agentes ACP y pydantic-ai; las escrituras igual necesitan tools. Modo de fallo: un
  backend no lista resources → memoria invisible. Descartada: rompe el requisito "igual para
  ambos backends".
- **C — Store con embeddings (retrieval semántico).** Vector store local + similitud. A favor:
  recupera por significado. En contra: dependencia nueva (modelo de embeddings / índice),
  más superficie, y el corpus por usuario es de **decenas** de memorias — el índice entero
  entra en el prompt. Sobre-ingeniería (YAGNI). Descartada por ahora; el diseño deja el seam
  para sumarlo en `store.search()` si algún día crece.
- **D — Reusar `manage_notes` key-value tal cual.** A favor: cero archivos nuevos. En contra:
  keyado por chat (no usuario), sin frontmatter/descripciones → no hay índice inyectable ni
  tipo ni auditoría; no es el patrón de referencia pedido. Descartada; en su lugar `notes` se
  convierte en alias deprecado de `manage_memory`.

## Decisión

**Alternativa A.** Es la más obvia y eficiente acá porque:
1. **Replica el patrón que el usuario pidió** (Claude Code/Hermes) y que ya vive en este repo:
   archivo-por-hecho + frontmatter + índice. El siguiente developer lo entiende sin explicación.
2. **Cumple el requisito MCP-first con el mínimo común denominador (tools).** Tools funcionan
   idénticos en ACP y pydantic-ai; resources no. Reusa el patrón exacto de los tools de journal
   (`server.py` `@mcp.tool()` + impl en `tools/`).
3. **Retrieval barato y suficiente:** el índice (una línea por memoria) se inyecta entero; el
   cuerpo se lee on-demand. Sin dependencias nuevas (descarta embeddings por YAGNI).
4. **Auditoría intrínseca:** al ser un store con un único punto de escritura
   (`store.write/delete`), el append al `audit.log` es trivial y no se puede saltear.

Trade-off aceptado a conciencia: retrieval por keyword, no semántico. Para un corpus de
decenas de memorias por usuario es irrelevante, y `store.search()` deja el seam para mejorar
sin cambiar la API del tool.

## Diseño

### Layout en disco (keyado por `user_id`)

```
data/memory/user_{user_id}/
  MEMORY.md            # índice inyectable: una línea por memoria (- [name] desc · type)
  memories/
    <slug>.md          # un hecho por archivo (frontmatter + cuerpo)
  audit.log            # JSONL append-only de toda escritura/edición/borrado
```

Frontmatter de cada `<slug>.md`:
```markdown
---
name: report-en-usd
description: El usuario siempre quiere los valores reportados en USD
type: preference            # preference | fact | feedback | reference
created: 2026-06-17
source: chat                # chat | agent:<slug>
---

Reportar todos los volúmenes y PnL en USD con separador de miles. Convertir BRL/EUR antes.
```

(`type` espeja la taxonomía de la memoria de referencia del repo; mantiene el modelo familiar.)

### Capa de store — `condor/memory/store.py`

Lógica pura de filesystem, **sin** dependencias de MCP/Telegram, reutilizable desde el proceso
main (para inyección) y desde el proceso MCP (para los tools):

```python
class MemoryStore:
    def __init__(self, user_id: int): ...
    def write(self, name: str, content: str, description: str,
              type: str = "fact", source: str = "chat") -> dict   # crea/sobrescribe + reindexa + audita
    def read(self, name: str) -> str | None                        # cuerpo completo
    def search(self, query: str, limit: int = 10) -> list[dict]    # keyword sobre name+description+body
    def list_index(self) -> str                                    # contenido de MEMORY.md (para inyectar)
    def delete(self, name: str, source: str = "user") -> bool      # borra + reindexa + audita
    def audit(self, limit: int = 30) -> list[dict]                 # últimas entradas del JSONL
    def _reindex(self) -> None                                     # regenera MEMORY.md desde memories/*.md
    def _append_audit(self, action: str, target: str, summary: str, source: str) -> None
```

- `write` es **atómico** (escribe a `.tmp` y `os.replace`) y siempre llama `_reindex()` después,
  de modo que `MEMORY.md` no puede quedar stale (mitiga el modo de fallo de A).
- `_reindex()` reconstruye el índice leyendo `memories/*.md` → robusto ante escrituras
  concurrentes (cada writer reconstruye desde la verdad en disco).
- `search()` hoy = keyword/substring sobre frontmatter+cuerpo; seam único para subir a
  embeddings sin tocar callers.
- Reusa el helper de parseo de frontmatter ya existente en el repo (mismo patrón que
  `condor/trading_agent/strategy.py` `_parse_frontmatter`).

`audit.log` formato (una línea JSON por evento):
```json
{"ts":"2026-06-17T12:00:00Z","source":"agent:grid_scalper","action":"write","target":"memory:report-en-usd","summary":"El usuario siempre quiere..."}
```

### Tool MCP — `mcp_servers/condor/tools/memory.py` + registro en `server.py`

Un único tool umbrella (espeja el estilo de `manage_notes`/`manage_trading_agent`):

```python
async def manage_memory(action: str, name: str | None = None, content: str | None = None,
                        description: str | None = None, type: str = "fact",
                        query: str | None = None, max_entries: int = 30) -> dict
```
Acciones: `write`, `read`, `search`, `list`, `delete`, `audit`.
- Resuelve `MemoryStore(settings.user_id)` (igual que `get_user_context` resuelve por settings).
- `source` se deriva de `settings.agent_slug` (`agent:<slug>` si está, si no `chat`) → la
  auditoría sabe quién escribió sin que el LLM lo informe.
- Registrado en `server.py` con `@mcp.tool()` + `@handle_errors("manage memory")`, con un
  docstring que es el contrato que ve el LLM (cuándo guardar, naming, etc.).

`manage_notes` (`server.py:175` + `tools/notes.py`) se convierte en **alias deprecado**: cada
acción mapea a `manage_memory` (`set`→`write` con `type="reference"`, `get`→`read`,
`list`→`list`, `delete`→`delete`) y el docstring marca DEPRECATED. Se mantiene un release para
no romper nada que lo llame; luego se elimina.

### Auto-inyección del índice

- **Chat** (`handlers/agents/_shared.py:402` `build_initial_context`): tras el system prompt y
  la info de servidor, agregar una sección `[USER MEMORY — what you remember about this user]`
  con `MemoryStore(user_id).list_index()` (corre en el proceso main, ya tiene `user_id`).
- **Trading agent** (`condor/trading_agent/prompts.py:118` `build_tick_prompt`): nuevo parámetro
  `user_memory: str` inyectado como sección `[USER MEMORY]` (junto a `[LEARNINGS]`). `engine.py`
  resuelve el `user_id` dueño del agente (ya lo pasa como `--user-id` al MCP) y lee
  `list_index()` una vez por tick, **cacheado** igual que la sección de routines (la memoria
  cambia poco entre ticks).

Si el índice está vacío, no se inyecta nada (cero ruido en usuarios nuevos).

### Enseñar a los assistants y al prompt del agente

- `assistants/condor.md` y `assistants/agent_builder.md`: reemplazar la línea muerta de
  `manage_notes` por una sección **MEMORY** que instruya:
  - Antes de responder, considerá `[USER MEMORY]`; leé el cuerpo con
    `manage_memory(action="read", name=...)` si necesitás detalle.
  - Cuando aprendas algo estable del usuario (preferencia, hecho, corrección), guardalo con
    `manage_memory(action="write", ...)` con una `description` de una línea. Guardá solo lo
    **nuevo y estable** (no efímero de la conversación). Estilo idéntico a la regla de
    learnings del trading agent (`prompts.py:54`).
- `condor/trading_agent/prompts.py` `BASE_PROMPT_COMMON`: agregar bloque MEMORY análogo al de
  JOURNAL, dejando claro que la memoria de **usuario** (preferencias/perfil) es distinta del
  **learning** operativo (market/execution sigue yendo a `trading_agent_journal_write`).

### Revisión por el usuario — `/memory` (Telegram) + acción `audit` del tool

`handlers/memory/__init__.py`: comando `/memory` que muestra el índice (`list`) y las últimas
entradas de `audit` con botones inline para borrar una memoria (callback → `store.delete(...,
source="user")`). Registrar el handler en `main.py` junto al resto. Es el camino del usuario
para "revisar/borrar"; el agente usa la misma data vía `manage_memory(action="audit")`.

### Migración — `scripts/migrate_notes_to_memory.py`

One-shot: por cada `data/notes/chat_{chat_id}.json`, resolver `user_id` desde el chat con
`config_manager` (`get_user_role`/ dueños), y por cada par key→value crear una memoria
`type="reference"` con `name=<key normalizada>` y `description=key`. Idempotente (si ya existe
el slug, no duplica). Loguea lo migrado. No borra los JSON (quedan como backup).

## Plan de implementación

- [x] **Slice 1 — store puro:** `condor/memory/store.py` con `MemoryStore` (write/read/search/
      list_index/delete/audit/_reindex/_append_audit) + tests unitarios sobre un tmpdir
      (write→list→read→search→delete→audit, atomicidad, dedup de slug). Sin tocar nada más.
- [x] **Slice 2 — tool MCP:** `tools/memory.py` + registrar `manage_memory` en `server.py`;
      convertir `manage_notes` en alias deprecado. Probar invocando el tool con `settings.user_id`.
- [x] **Slice 3 — inyección chat:** sección `[USER MEMORY]` en `build_initial_context`; enseñar
      `assistants/condor.md` + `assistants/agent_builder.md`. Verificar en una sesión `/agent`
      que el índice aparece y que el agente guarda/recuerda.
- [x] **Slice 4 — inyección trading agent:** parámetro `user_memory` en `build_tick_prompt` +
      lectura cacheada en `engine.py` + bloque MEMORY en `BASE_PROMPT_COMMON`. Verificar con un
      dry-run que el tick ve `[USER MEMORY]`.
- [x] **Slice 5 — revisión usuario:** `/memory` en `handlers/memory/` + registro en `main.py`
      (índice + audit + borrar). Acción `audit` del tool ya cubierta en slice 2.
- [x] **Slice 6 — migración:** `scripts/migrate_notes_to_memory.py`, correr sobre los
      `data/notes/*.json` reales, verificar que aparecen como memorias.

## Criterio de aceptación

- [x] `manage_memory` (write/read/search/list/delete/audit) funciona invocado por ACPClient
      **y** por PydanticAIClient (sin código por-cliente). El tool vive en el server MCP común
      que ambos backends consumen; no se agregó nada por-cliente. Verificado por invocación
      directa con `settings.user_id`.
- [x] Un hecho guardado en una sesión `/agent` aparece en `[USER MEMORY]` de una sesión nueva
      del mismo usuario **y** en el tick de un trading agent de ese usuario. Verificado:
      `build_initial_context` y `build_tick_prompt` inyectan el índice del mismo `MemoryStore(user_id)`.
- [x] Toda escritura/edición/borrado queda en `audit.log` con `source` correcto (chat vs
      `agent:<slug>`); `/memory` lo muestra y permite borrar.
- [x] `MEMORY.md` nunca queda stale respecto de `memories/*.md` (se regenera en cada escritura;
      test `test_reindex_never_stale` + self-heal en `list_index`).
- [x] Migración de `data/notes/*.json` produce memorias equivalentes (3 migradas, idempotente);
      `manage_notes` sigue funcionando como alias deprecado de `manage_memory`.
- [x] El journal operativo del trading agent (learnings/decisiones/snapshots) **no cambia**;
      `build_tick_prompt` solo suma un parámetro opcional `user_memory` (default `""`), los
      ticks sin memoria quedan idénticos.
- [x] Tests unitarios del store (12, todos verdes); el resto verificado con builders de prompt
      e invocación de tools.

## Riesgos y notas

- **Poisoning / feedback loop:** el agente escribe memoria que después lee. Mitigación: la
  memoria es advisory (nunca ejecuta), el `type` distingue `preference/fact/feedback/reference`,
  y `/memory` + `audit.log` permiten al usuario podar. Las acciones de trading siguen gateadas
  por el risk engine existente (`condor/trading_agent/risk.py`), no por la memoria.
- **chat_id ≠ user_id:** hoy `notes` keyea por chat. La memoria keyea por **user_id** (decisión:
  memoria de usuario). `settings.user_id` ya viene resuelto en el proceso MCP; en chats de grupo
  varios usuarios comparten chat pero cada uno tiene su `user_id` → semántica correcta.
- **Concurrencia:** ticks del mismo usuario + chat pueden escribir a la vez. `write` atómico +
  `_reindex()` desde disco lo absorbe; colisión de mismo slug = last-write-wins (aceptable).
- **Relación con learnings operativos:** queda abierto (futuro) promover un learning operativo
  generalizable a memoria de usuario. Fuera de alcance; el seam (`source="agent:<slug>"`) ya
  queda listo.
- Dependencia inversa: **[[FEAT-002]]** (skills) construye sobre este store y su patrón de
  índice+auditoría.

## Desvíos respecto al diseño (durante implementación)

- **Lectura del índice en el trading agent: fresca por tick, no cacheada.** El diseño proponía
  cachear `list_index()` "igual que la sección de routines" (cache de sesión). Se optó por leer
  el índice fresco cada tick en `engine.py`: es un único read de un archivo chico (como ya se
  leen learnings/summary cada tick) y garantiza que una memoria escrita por el chat —o por el
  propio agente— aparezca en el siguiente tick sin esperar a una sesión nueva. El costo en
  tokens es idéntico (se inyecta el mismo índice); solo cambia un I/O trivial. Mejora el
  criterio de aceptación de propagación cross-superficie.
- **Store sin dependencia de `trading_agent`.** En vez de importar `_parse_frontmatter` desde
  `strategy.py`, se reimplementaron helpers locales mínimos en `store.py` para mantenerlo como
  "lógica pura de filesystem sin dependencias" (como pedía el Diseño). Mismo patrón, sin acoplar.
- **Sin tests previos en el repo:** se creó `tests/` con `pytest` (12 tests del store). El
  proyecto no tenía carpeta de tests; se sigue el runner ya declarado en `pyproject` (`pytest`).
- **Rama:** la feature se implementó en `feature/FEAT-001-user-memory` (la rama activa al iniciar,
  `improvements/frontend-wave-1`, era de otro tema; se aisló para trazabilidad limpia).

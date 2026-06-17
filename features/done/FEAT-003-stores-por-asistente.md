---
id: FEAT-003
title: Memoria y skills por-asistente — cada asistente (condor chat + cada trading agent) tiene su propio store
status: done
effort: L
risk: high
new_files:
  - condor/memory/paths.py                 # resolver store_root(user_id, agent_slug) + iter_user_stores
  - scripts/migrate_to_per_assistant_stores.py
touched_files:
  - condor/memory/store.py                 # MemoryStore.__init__ acepta agent_slug; root vía paths.py
  - condor/memory/skills.py                # SkillStore.__init__ acepta agent_slug; root vía paths.py
  - mcp_servers/condor/tools/memory.py:16  # _store() resuelve por settings.agent_slug
  - mcp_servers/condor/tools/skills.py:16  # _store() resuelve por settings.agent_slug
  - condor/trading_agent/engine.py:308     # MemoryStore/SkillStore(user_id, strategy.slug)
  - handlers/agents/_shared.py:73          # loader: descubrir assistants/{name}/AGENT.md (forma carpeta)
  - handlers/agents/_shared.py:551         # blurb [USER MEMORY]/[SKILLS]: quitar "shared with trading agents"
  - handlers/memory/__init__.py:24         # /memory agrega sobre todos los stores del usuario
  - main.py:525                            # watcher recursivo + reload de AGENT.md
  - assistants/condor.md                   # mover a assistants/condor/AGENT.md
  - .gitignore                             # ignorar assistants/*/store/ y trading_agents/*/store/
depends_on:
  - "[[FEAT-001]]"
  - "[[FEAT-002]]"
commits:
  - "781f5a6 (feat) per-assistant memory/skill store resolver"
  - "f4f80a4 (feat) resolve memory/skill stores per assistant in callers"
  - "6fc2b26 (feat) assistant folder form + move condor to AGENT.md"
  - "3c20af9 (feat) group /memory by assistant"
  - "82493c3 (feat) archive legacy store on migrate; gitignore runtime stores"
created: 2026-06-17
---

## Objetivo

Hoy la memoria (FEAT-001) y las skills (FEAT-002) son **por-usuario y compartidas**: el chat
`/agent` y todos los trading agents de un usuario leen/escriben el mismo store
(`data/memory/user_{id}/`). Esta feature las vuelve **por-asistente**: cada asistente tiene su
propio store de memoria + skills, sin compartir nada entre asistentes.

Asistentes:
- **El chat `condor`** (la cara interactiva de `/agent`) → un store propio.
- **Cada trading agent** (cada estrategia bajo `trading_agents/{slug}/`) → un store propio.

Capacidades observables al cerrar:
- Una skill/memoria que aprende el `grid_scalper` **no** aparece en el `ema_trend_follower` ni en
  el chat `condor`, y viceversa. El índice `[SKILLS]`/`[USER MEMORY]` de cada tick/sesión solo
  trae lo de **ese** asistente.
- En disco, todo lo de un asistente vive junto a su definición: `assistants/condor/AGENT.md` +
  su store; `trading_agents/{slug}/AGENT.md` + su store.
- `/memory` sigue dejando al usuario revisar/borrar, ahora **agrupado por asistente**.

**Fuera de alcance:**
- Una capa "global" de skills compartidas entre asistentes (el usuario eligió aislamiento total).
- Migrar el contenido existente: por decisión del usuario **todos arrancan vacíos** (el store
  actual se archiva como backup, no se copia).
- El journal operativo del trading agent (`learnings.md`, `sessions/`) — ya es por-agente y **no
  se toca**.
- Memoria/skills por-**modo** del chat (agent_builder / routine_builder): comparten el store del
  chat `condor` (ver Decisión).

## Contexto y restricciones

- **El key del store hoy es solo `user_id`.** `MemoryStore.__init__(self, user_id)` arma
  `_DATA_ROOT/user_{id}` (`condor/memory/store.py:103-108`); `SkillStore` igual
  (`skills.py:58-63`). Todos los callers pasan solo `user_id`:
  - MCP: `MemoryStore(settings.user_id)` / `SkillStore(settings.user_id)`
    (`tools/memory.py:16`, `tools/skills.py:16`).
  - Chat: `MemoryStore(user_id)` / `SkillStore(user_id)` en `build_initial_context`
    (`_shared.py:551,569`).
  - Trading agent: `MemoryStore(self.user_id)` / `SkillStore(self.user_id)` por tick
    (`engine.py:308,311`).
  - Revisión: `MemoryStore(user_id)` / `SkillStore(user_id)` en `handlers/memory/__init__.py:26-27`.
- **La identidad del asistente ya viaja al MCP.** `settings.agent_slug` (`settings.py:13,30`) es
  el slug de la estrategia para un trading agent, y vacío para el chat. Se setea en
  `build_mcp_servers_for_agent(..., agent_slug=strategy.slug)` (`_shared.py:399,493`) y queda
  `None` para el chat. → la identidad **ya está disponible** donde se construyen los stores; no
  hay que tocar el wire MCP.
- **El engine ya conoce su identidad.** `TickEngine` tiene `self.strategy.slug` y
  `self.strategy.agent_dir` (= `trading_agents/{slug}/`) (`engine.py:92`).
- **Multi-tenant es real y obligatorio para el chat.** FEAT-001 (riesgos, línea ~307) lo deja
  explícito: en chats de grupo varios usuarios comparten chat pero cada uno tiene su `user_id`; la
  memoria del chat **debe** seguir siendo por-usuario. → "por-asistente" **compone con** `user_id`,
  no lo reemplaza. El key pasa de `(user_id)` a `(asistente, user_id)`.
- **`assistants/` es código commiteado; `data/` está gitignored** (`.gitignore:172`). Los prompts
  de asistente (`assistants/condor.md`) se versionan; el store es runtime. Co-locar el store bajo
  `assistants/condor/` exige una regla nueva de `.gitignore` para no commitear runtime.
- **El loader de asistentes globea `assistants/*.md`** (`_shared.py:73`,
  `_load_assistant_full:56`). Pasar a `assistants/condor/AGENT.md` requiere enseñarle la forma
  carpeta. `main.py:525` ya watchea `assistants/` para auto-reload.
- **Los trading agents ya mezclan definición + runtime** en `trading_agents/{slug}/` (`agent.md`
  commiteado-ish junto a `sessions/`, `learnings.md`, `routines/`). Co-locar el store ahí es
  coherente con el patrón existente; `trading_agents/` hoy está untracked.
- **Lectura del índice por tick es fresca** (FEAT-002 desvío, `engine.py:300-313`): se mantiene;
  solo cambia el root del que se lee.

Restricciones:
- El store sigue siendo **lógica pura de filesystem sin deps de MCP/Telegram** (FEAT-001) → el
  resolver de paths vive en `condor/memory/` y lo usan tanto el proceso main como el subproceso MCP.
- Memoria/skills siguen siendo **advisory**; la ejecución sigue gateada por el risk engine.
- Cero cambios al contrato de los tools `manage_memory`/`manage_skill` que ve el LLM (el LLM no
  sabe ni le importa dónde vive el store; lo resuelve el wrapper por `settings.agent_slug`).

## Alternativas consideradas

- **A — Key `(asistente, user_id)`; store co-locado bajo el home del asistente; identidad derivada
  de `agent_slug` (elegida).** Un resolver `store_root(user_id, agent_slug)` mapea
  `agent_slug` → `trading_agents/{slug}/store/user_{id}/`, y `None` → `assistants/condor/store/user_{id}/`.
  Los dos stores toman ese root. A favor: cambio mínimo y localizado (la identidad **ya** viaja por
  `settings.agent_slug` y el engine ya tiene el slug); co-loca todo lo de un asistente en su carpeta
  como pidió el usuario; un único punto que decide paths. En contra: hay que enseñarle al loader la
  forma carpeta y agregar una regla de `.gitignore`; `/memory` debe agregar sobre varios stores.
  Modo de fallo: dos asistentes resuelven al mismo root → se evita porque el slug es único y el chat
  usa el sentinel fijo `condor`.
- **B — Mantener un store por-usuario y agregar un campo `assistant` por registro + filtrar en la
  inyección.** A favor: cero cambios de path, `.gitignore` intacto, `/memory` casi igual. En contra:
  **no** entrega la estructura de carpetas que el usuario pidió explícitamente; el `audit.log` y los
  borrados quedan comingled; la inyección tiene que filtrar en memoria y el aislamiento es "lógico",
  no físico (fácil de romper por un caller que olvide filtrar). Descartada: no cumple el objetivo.
- **C — Árbol runtime separado `data/assistants/{id}/user_{uid}/...` (NO bajo `assistants/`).**
  A favor: separación commiteado/runtime más limpia, sin tocar `.gitignore` dentro de `assistants/`.
  En contra: rompe la co-locación que el usuario dibujó (la definición y su store en carpetas
  distintas); para entender un asistente hay que mirar dos lugares. Descartada por la decisión de
  co-locar; es el runner-up si algún día molesta la regla de `.gitignore`.

## Decisión

**Alternativa A.** Es la más obvia y eficiente acá porque la pieza cara —saber *qué asistente* está
escribiendo— **ya está resuelta**: `settings.agent_slug` viaja al MCP y el engine ya tiene
`strategy.slug`. Todo el cambio se reduce a (1) un resolver de paths central y (2) pasarle el slug a
los constructores de los stores en los 4 puntos que hoy pasan solo `user_id`. Co-locar bajo el home
del asistente cumple la estructura que pidió el usuario y sigue el patrón que `trading_agents/{slug}/`
ya usa (definición + runtime juntos). El aislamiento es **físico** (carpetas distintas), no un filtro
que un caller pueda olvidar.

Trade-offs aceptados a conciencia:
- **Se pierde el compartir cross-asistente** (era el valor central de FEAT-001/002). Es exactamente
  lo que el usuario pidió: cada asistente acumula su propio know-how/perfil.
- **`store/user_{id}/` se interpone** entre el home del asistente y los archivos (en vez de
  `condor/memory/` y `condor/skills/` directos como en el dibujo). Se justifica por (a) multi-tenant:
  el chat necesita `user_{id}` sí o sí; (b) separar runtime (gitignored) de la definición commiteada
  `AGENT.md` con una sola regla de ignore. Se mantiene **uniforme** en chat y trading agents aunque un
  trading agent tenga de hecho un solo owner.
- **Los modos builder del chat** (`agent_builder`, `routine_builder`) comparten el store del chat
  `condor`. Son variantes efímeras del asistente de chat, no dueñas de know-how persistente; darles
  store propio sería ruido. Si en el futuro lo ameritan, el resolver ya deja el seam (recibir el
  nombre del modo en vez del sentinel fijo).

## Diseño

### Layout en disco (key = asistente + user)

```
# Chat asistente "condor"
assistants/condor/AGENT.md                          # definición (commiteada) — renombrado de condor.md
assistants/condor/store/user_{user_id}/             # runtime (gitignored)
  MEMORY.md  memories/<slug>.md
  skills/SKILLS.md  skills/<slug>/SKILL.md
  audit.log

# Cada trading agent
trading_agents/{slug}/AGENT.md                      # definición (existe como agent.md; ver slice 5)
trading_agents/{slug}/store/user_{user_id}/         # runtime
  MEMORY.md  memories/...  skills/...  audit.log
trading_agents/{slug}/{sessions,dry_runs,routines,learnings.md}   # journal operativo (sin cambios)
```

### Resolver central — `condor/memory/paths.py` (nuevo)

```python
_PROJECT_ROOT = Path(__file__).parent.parent.parent

def store_root(user_id: int, agent_slug: str | None = None) -> Path:
    """Root del store por-usuario de un asistente.
    agent_slug seteado -> trading agent:  trading_agents/{slug}/store/user_{id}
    agent_slug None     -> chat condor:    assistants/condor/store/user_{id}
    """
    if agent_slug:
        base = _PROJECT_ROOT / "trading_agents" / agent_slug
    else:
        base = _PROJECT_ROOT / "assistants" / "condor"
    return base / "store" / f"user_{user_id}"

def iter_user_stores(user_id: int) -> list[tuple[str, Path]]:
    """(label, root) de cada store existente del usuario, para /memory.
    Escanea assistants/*/store/user_{id} y trading_agents/*/store/user_{id}."""
```

### Stores — `store.py` / `skills.py`

Constructores aceptan el slug y derivan el root del resolver (resto del archivo intacto: índice,
auditoría, escritura atómica, self-heal):

```python
class MemoryStore:
    def __init__(self, user_id: int, agent_slug: str | None = None):
        self.user_id = user_id
        self.root = store_root(user_id, agent_slug)
        self.memories_dir = self.root / "memories"
        self.index_file = self.root / "MEMORY.md"
        self.audit_file = self.root / "audit.log"

class SkillStore:
    def __init__(self, user_id: int, agent_slug: str | None = None):
        self.user_id = user_id
        self.root = store_root(user_id, agent_slug)
        self.skills_dir = self.root / "skills"
        self.index_file = self.skills_dir / "SKILLS.md"
        self.audit_file = self.root / "audit.log"
```

`_DATA_ROOT` deja de usarse para construir el root (lo reemplaza el resolver). El `audit.log`
compartido memoria↔skills se mantiene: ahora hay uno por (asistente, user).

### Callers

- **MCP** (`tools/memory.py:16`, `tools/skills.py:16`):
  `MemoryStore(settings.user_id, settings.agent_slug or None)` (idem skills). `_source()` no cambia.
- **Chat** (`_shared.py:551,569`): `MemoryStore(user_id)` / `SkillStore(user_id)` quedan **igual**
  (agent_slug por defecto `None` → resuelve a `assistants/condor/...`). Solo se actualiza el texto
  del blurb (quitar "shared with the user's trading agents").
- **Trading agent** (`engine.py:308,311`): `MemoryStore(self.user_id, self.strategy.slug)` /
  `SkillStore(self.user_id, self.strategy.slug)`.
- **`/memory`** (`handlers/memory/__init__.py`): `_build_view(user_id)` itera `iter_user_stores`
  y arma una sección por asistente (índice de memoria + skills + audit, con botones de borrado que
  incluyen el asistente en el callback: `memory:delete:{slug}@{assistant}`), en vez de un único store.

### Loader de asistentes — forma carpeta (`_shared.py`)

`_load_assistant_full(name)`: probar `assistants/{name}.md` y, si no, `assistants/{name}/AGENT.md`.
`discover_assistants()`: globear `assistants/*.md` **y** `assistants/*/AGENT.md`. `main.py:525` ya
watchea `assistants/`; asegurar que el watch sea recursivo y que el reload dispare con cambios en
`AGENT.md`. Se soportan **ambas** formas (archivo plano y carpeta) → solo `condor` se mueve a carpeta;
`agent_builder.md`/`routine_builder.md` pueden quedar planos.

### Migración — `scripts/migrate_to_per_assistant_stores.py`

"Todos arrancan vacíos": mover `data/memory/user_*` a `data/memory/_archive_pre_FEAT003/` (backup,
no se borra). Los stores nuevos se crean lazy en el primer write. Idempotente (si ya está archivado,
no hace nada). `.gitignore`: agregar `assistants/*/store/` y `trading_agents/*/store/`.

## Plan de implementación

- [x] **Slice 1 — resolver + stores:** crear `condor/memory/paths.py` (`store_root`,
      `iter_user_stores`); cambiar `MemoryStore`/`SkillStore.__init__` para tomar `agent_slug` y usar
      el resolver. Tests: dos slugs distintos → roots distintos; chat (None) → `assistants/condor`;
      mismo (slug,user) → mismo root; round-trip write→list→read aislado entre dos asistentes.
- [x] **Slice 2 — callers MCP + engine:** `tools/memory.py`/`tools/skills.py` resuelven por
      `settings.agent_slug`; `engine.py` pasa `self.strategy.slug`. Verificar: una skill creada por un
      tick del `grid_scalper` aparece en el siguiente tick del `grid_scalper` y **no** en el chat.
- [x] **Slice 3 — loader forma carpeta + mover condor:** soportar `assistants/{name}/AGENT.md` en el
      loader + watcher recursivo; `git mv assistants/condor.md assistants/condor/AGENT.md`. Verificar
      que `/agent` levanta el prompt de condor y auto-reload sigue andando.
- [x] **Slice 4 — `/memory` agregado por asistente + blurbs:** `iter_user_stores` en `_build_view`;
      callbacks de borrado con asistente; actualizar el texto `[USER MEMORY]`/`[SKILLS]` (chat y
      `prompts.py`) para quitar el "compartido con trading agents".
- [x] **Slice 5 — migración + gitignore + (opcional) AGENT.md en trading agents:**
      `scripts/migrate_to_per_assistant_stores.py` (archivar store actual) + reglas de `.gitignore`.
      Opcional/cosmético: renombrar `trading_agents/{slug}/agent.md` → `AGENT.md` + ajustar el loader
      de `strategy.py` para simetría con el chat (se puede diferir; es el slice de menos valor).
      **Diferido** — el rename `agent.md`→`AGENT.md` de los trading agents no se hizo (cosmético, sin
      valor funcional; el loader de `strategy.py` sigue usando `agent.md` y el store es independiente).

## Criterio de aceptación

- [x] Una memoria/skill creada por el asistente X (chat o un trading agent) **no** aparece en el
      índice inyectado de ningún otro asistente del mismo usuario; sí aparece en el de X (sesión nueva
      o tick siguiente).
- [x] Dos trading agents distintos del mismo usuario tienen stores físicamente separados
      (`trading_agents/{slug}/store/user_{id}/`), con `audit.log` independientes.
- [x] El chat `condor` resuelve a `assistants/condor/store/user_{id}/` y sigue siendo **por-usuario**
      (dos `user_id` en el mismo chat no se mezclan).
- [x] `manage_memory`/`manage_skill` funcionan igual vía ACPClient y PydanticAIClient (sin cambios de
      contrato); el LLM no necesita saber el path.
- [x] `/memory` muestra y permite borrar memorias/skills **agrupadas por asistente**; los borrados
      quedan en el `audit.log` del asistente correcto (`source="user"`).
- [x] El prompt de `condor` se carga desde `assistants/condor/AGENT.md` y el auto-reload sigue
      funcionando; `agent_builder`/`routine_builder` (planos) siguen cargando.
- [x] La migración archiva el store actual y los asistentes arrancan vacíos; runtime nuevo no se
      commitea (gitignore). El journal operativo del trading agent no cambia.
- [x] Tests del resolver + aislamiento entre stores (verde).

## Riesgos y notas

- **Pérdida de compartir cross-asistente** — es la decisión del usuario, no un bug. Documentarlo en
  los assistants (quitar el wording "compartido") para no confundir al LLM ni al próximo dev.
- **`/memory` se vuelve multi-store** — riesgo de UI ruidosa si el usuario tiene muchos trading
  agents. Mitigación: una sección colapsable por asistente, mostrar solo asistentes con store no
  vacío (vía `iter_user_stores`).
- **Colisión de root** — dos asistentes nunca deben resolver al mismo path: el slug de estrategia es
  único y el chat usa el sentinel fijo `condor`. Un trading agent llamado literalmente `condor`
  colisionaría con el chat → validar/rechazar ese slug al crear estrategias (chequear en
  `strategy.py`/builder), o ubicar el chat en `assistants/_condor_chat/`. Resolver al implementar.
- **Multi-tenant en trading agents** — se mantiene `user_{id}` por uniformidad aunque un trading
  agent tenga de hecho un solo owner; sin costo real y a prueba de futuro si una estrategia se
  compartiera entre usuarios.
- **Modos builder comparten el store del chat** — simplificación deliberada; el resolver deja el seam
  para darles store propio si alguna vez lo necesitan.
- **Limpieza de `notes` (independiente):** `manage_notes` sigue siendo un alias deprecado y
  `data/notes/` un backup muerto (ver FEAT-001). No es parte de esta feature, pero conviene cerrarlo
  en paralelo para no arrastrar dos vías muertas.
- **Depende de [[FEAT-001]] y [[FEAT-002]]** (el substrato que esta feature re-scopea). No romper el
  contrato de los tools ni el formato de archivos: solo cambia *dónde* viven.

### Desvíos al implementar (FEAT-003)

- **Colisión de root: no se materializa.** El chat vive bajo `assistants/condor/` y los trading
  agents bajo `trading_agents/{slug}/` — son top-levels distintos, así que ni un agente llamado
  literalmente `condor` colisiona con el chat. No hizo falta validar/rechazar el slug ni reubicar el
  chat. El riesgo del diseño asumía un layout que finalmente no se usó.
- **Watcher: se excluye `store/`.** Co-locar el store del chat bajo `assistants/condor/store/` lo metía
  dentro del árbol que `main.py` watchea (recursivo por defecto en `awatch`), así que *cada* write de
  memoria del chat disparaba un reload completo de handlers. Se agregó un `watch_filter`
  (`DefaultFilter` + ignora cualquier path con `/store/`) — los cambios en `AGENT.md`/`*.md` siguen
  recargando. No estaba en el diseño; es corrección necesaria por la co-locación.
- **`prompts.py` no necesitó cambios de wording.** El texto `[USER MEMORY]`/`SKILLS` del trading agent
  ya hablaba "del OWNER", sin afirmar que se compartía con otros asistentes. Solo se ajustaron los
  comentarios del blurb del chat en `_shared.py`.
- **Trazabilidad del rename.** El `git mv condor.md → condor/AGENT.md` quedó pre-staged por el `git mv`
  y se materializó en el commit del Slice 1 (`781f5a6`, move 0-content) aunque conceptualmente es del
  Slice 3 (`6fc2b26`, que trae el loader forma-carpeta que lo hace funcional). Estado final correcto.
- **Slice 5 opcional diferido:** no se renombró `trading_agents/{slug}/agent.md` → `AGENT.md`
  (cosmético, sin valor funcional).
- **Migración ejecutada localmente:** se archivaron 2 stores legacy (`user_481175164`,
  `user_6310433268`) a `data/memory/_archive_pre_FEAT003/`; idempotente al re-correr.

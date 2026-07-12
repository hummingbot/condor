# Refactor 06 — dissolve the assistants layer: `CONDOR.md` at repo root

Status: **proposed** (2026-07-11) · The refactor that was deferred during the
01b/02 series because "root AGENT.md collides with host conventions" — the
`CONDOR.md` naming resolves exactly that hazard.

## 1. What the assistants layer actually is today

`assistants/` was built as a generic multi-assistant system (FEAT-003): each
assistant gets a definition + co-located store, auto-discovered into a
Telegram "mode" picker. Reality check:

- **Exactly one assistant exists** — `condor`, the chat coordinator — and
  FEAT-004 already decided there will only ever be one: new capabilities
  ship as *skills* of the single interactive agent, not as separate
  selectable assistants.
- **The layer is already half-dissolved.** The chat's skills moved to the
  repo-root `skills/` (refactor-05) and its routines have always been the
  repo-root `routines/`. All that still lives in `assistants/condor/` is
  the brain (`AGENT.md`, ~130 lines) and the memory (`store/user_{id}/`,
  gitignored runtime data).
- **The machinery it carries** for that one instance: flat-vs-folder file
  resolution (`_assistant_path`), `discover_assistants()`,
  `AGENT_MODES` + `normalize_mode()` + a mode picker in the Telegram menu
  + a `mode` field threaded through chat-session plumbing
  (`handlers/agents/__init__.py`, `chat_ws.py`) — a multi-mode system
  whose menu has one entry.

## 2. The proposal

Move the chat assistant's two remaining artifacts to the repo root and
delete the layer:

```
CONDOR.md                # was assistants/condor/AGENT.md — the chat brain
                         #   (keeps its frontmatter: label, description,
                         #    agent_key — _default_agent() reads it)
skills/                  # already root (refactor-05)
routines/                # already root (always was)
store/user_{id}/         # was assistants/condor/store/ — gitignored
agents/{slug}/           # the specialists, exactly as today
    AGENT.md  skills/  routines/  store/  ...
```

### Why `CONDOR.md` and not root `AGENT.md`

Root `AGENT.md` / `AGENTS.md` are **host-owned names**: the agents.md
standard (OpenClaw, Codex, others) and Claude Code's context loading treat
them as workspace instructions. Under the repo-as-workspace deployment
(refactor-05 §4.0 — users open Claude Code/OpenClaw/Hermes *in this repo*),
a root `AGENT.md` containing Condor's chat system prompt would be slurped
into the host's own context: the host would be told it *is* Condor, has
Telegram users, and should keep tool chains short — role confusion baked
into the filesystem. `CONDOR.md` is read by no harness natively, so the
brain stays private to Condor's own loader while remaining exactly one
obvious file for a human to open. Same reasoning as `CLAUDE.md`: a
product-named file for a product-scoped prompt.

### What it buys

1. **The root becomes the coordinator's agent-home, symmetric with its
   specialists.** `CONDOR.md + skills/ + routines/ + store/` at root
   mirrors `agents/{slug}/{AGENT.md, skills/, routines/, store/}`
   one-for-one. The ontology finally reads off the filesystem: *the repo
   root is the chat agent; `agents/` are the domain agents it routes to.*
   One taxonomy instead of two (`assistants/` vs `agents/`).
2. **Net-negative code.** `paths.py` loses its chat-vs-agent branch
   (`store_root(user_id, None)` → root `store/`), `_shared.py` loses
   `_assistant_path` flat/folder resolution and `discover_assistants`,
   and the mode machinery (`AGENT_MODES`, `normalize_mode`, the menu
   entry, the threaded `mode` value) collapses to the constant it always
   was. This matches the curation precedent: unused generality gets
   removed, not kept dormant.
3. **Kills a real confusion.** Docs currently need a footnote every time
   ("the chat coordinator remains a separate schema…"). New-contributor
   question #1 — "what's the difference between an assistant and an
   agent?" — stops existing.

### Costs / risks

- **Store migration**: one real user store exists
  (`assistants/condor/store/user_456181693/`) — a `git mv`-less move of
  gitignored data plus a `.gitignore` update (`assistants/*/store/` →
  `store/`). Trivial but touching live user memories, so back up first.
- **Mode plumbing removal** is the only non-mechanical part: `mode` is
  threaded through Telegram session handling and the web chat
  (`chat_ws.py`). Removing the *parameter* everywhere is the honest
  endpoint but touches the chat's hot path; hardcoding the loader to
  `CONDOR.md` first and deleting the plumbing second is the safe
  staging.
- **Root file count** grows by one file + one dir (`CONDOR.md`,
  `store/`). Acceptable — root is already the chat's home in spirit.

## 3. The keep-it case, evaluated honestly

Arguments for leaving `assistants/` alone:

1. **Zero migration risk.** It works today; the collision hazard is
   *inside* the dir, i.e. already contained.
2. **Future multi-assistant slot.** If a second persona ever appears
   (a "research desk" chat, a per-team assistant), the layer is ready.
3. **Explicit privacy boundary.** `assistants/` visibly marks "not
   host-facing" without relying on hosts not knowing the name
   `CONDOR.md`.

Why these don't hold up:

1. Migration risk is one gitignored dir move and a loader path change —
   smaller than migrations already executed this week (01b, 07).
2. The future slot is a bet the architecture has **already bet against**:
   FEAT-004 ships capabilities as skills; refactor-05 moved the chat's
   skills out; multi-persona would be a *skills/prompt* concern, not a
   directory taxonomy. Keeping scaffolding for a rejected design is
   exactly the pattern the curation removal established we don't do.
3. The privacy boundary argument is real but weak: nothing sensitive is
   in `CONDOR.md` (the store is gitignored either way), and "hosts read
   root AGENT.md but not CONDOR.md" is a fact of the standards, not a
   guess — the same fact `CLAUDE.md` relies on.

The one scenario where **keep** wins: if we expect hosts to start
indexing arbitrary root `*.md` files into context. No current harness
does (they load named files: CLAUDE.md, AGENTS.md, AGENT.md), and if one
ever did, `assistants/condor/AGENT.md` would be equally exposed via
directory scans — the mitigation there is the same either way (nothing
secret in the prompt).

## 4. Recommendation

**Dissolve it.** The layer is a two-file vestige carrying a
multi-assistant machine with one tenant, and the `CONDOR.md` naming
removes the only hazard that kept this deferred. Implementation order:

1. Move `assistants/condor/AGENT.md` → `CONDOR.md`; point the loader at
   it (drop flat/folder resolution); keep frontmatter semantics.
2. Move `assistants/condor/store/` → `store/`; update `paths.py`
   (`store_root`, `iter_user_stores`) + `.gitignore`; back up the user
   store first.
3. Delete the mode machinery (`discover_assistants`, `AGENT_MODES`,
   `normalize_mode`, the menu entry) and the `mode` parameter it fed —
   hardcode the single brain. This step touches chat plumbing; do it as
   its own commit so it reverts independently.
4. Remove the empty `assistants/` dir; update docs
   (`simpler-agent-framework.md` §1's coordinator footnote becomes the
   symmetry statement).

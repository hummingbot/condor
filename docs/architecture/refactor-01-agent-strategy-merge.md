# Refactor 01 — Merge Strategy into Agent (one strategy per agent)

Status: **proposed** · Branch: `spike/simpler-agent-framework` · Scope: first of a
planned series of framework simplifications. · Alternative under
consideration: [refactor-01b](refactor-01b-agent-history-multi-strategy.md)
(same agent-level history unification, but strategies kept as pure playbook
templates — preserves playbook A/B testing at the cost of keeping a slimmed
strategy tier).

## 1. Goal

Collapse the Agent/Strategy two-level hierarchy into a single entity:

- **One strategy per agent.** The `strategy.md` body and config merge into
  `AGENT.md`. An agent that trades on a loop *is* its playbook.
- **Learnings, sessions, and dry runs move to the agent level** —
  `agents/{slug}/learnings.md`, `agents/{slug}/sessions/`, `agents/{slug}/dry_runs/`.
- **Every invocable agent gets sessions.** If Condor can `delegate()` (or
  `consult()`) an agent, each run should leave an analyzable session on disk —
  today `routine_builder`'s delegations are flat transcript files and consults
  leave nothing. Sessions become the uniform envelope for *all* agent runs:
  tick loops, delegations, and (optionally, see §10) consults.

### Why this is cheap to do right now

Grounded in the current working tree:

- **No `sessions/` directory exists anywhere under `agents/`.** The only
  operational history is 5 `dry_runs/experiment_N.md` files (4 under
  `market_making_expert/strategies/pmm_mister_operator/`, 1 under
  `funding_rate_watcher/strategies/funding_snapshot/`) and 4 delegation
  transcripts. There is no session history to migrate and no historical
  executor attribution to preserve.
- **Every agent has ≤ 1 strategy.** `market_making_expert` → `pmm_mister_operator`,
  `funding_rate_watcher` → `funding_snapshot`, `routine_builder` and
  `revival_trader` → none. The multi-strategy capability is designed but unused.
- **`Strategy.skills` is vestigial** — serialized in `_save()`, never read by the
  engine or prompt builder (skills come from `SkillStore(agent.slug)`).
- Multiple modules already carry a "legacy dotless prefix → `agents/{slug}/`"
  fallback (`journal.resolve_agent_dirs`, `condor_client.agent_strategy_from_agent_id`).
  After the merge, **the legacy path becomes the only path** — the fallback code
  is deleted, not extended.

## 2. Current state (what the merge removes)

```
agents/{slug}/
    AGENT.md                        # identity: name, tools, when_to_consult, agent_key…
    skills/  routines/  store/      # the shared "brain"
    delegations/{task_id}.md        # flat delegate transcripts (consults: nothing)
    strategies/{sslug}/
        strategy.md                 # tick playbook + default_config frontmatter
        config.yml                  # runtime config
        learnings.md                # cross-session learnings
        sessions/session_N/         # journal.md + snapshots/  (per tick-loop run)
        dry_runs/experiment_N.md    # one-shot dry_run / run_once snapshots
        shutdown.md                 # optional winddown override
```

Composite identity plumbing that exists only to support the two levels:

- `strategy_id` / run key `"{agent_slug}.{strategy_slug}"` (`Strategy.key`,
  `split_key`, `runkey_from_agent_id`, `agent_strategy_from_agent_id`)
- session run id (`agent_id`) `"{agent_slug}.{strategy_slug}_{N}"` / `"..._e{N}"`,
  stamped as `controller_id` on executors for PnL attribution
- model override triad `config.agent_key > strategy.agent_key > agent.agent_key`
- shutdown policy walk `strategy → agent → _defaults`
- web routes `/agents/{slug}/strategies/{sslug}/...` (14 endpoints) + a whole
  `StrategyDetail.tsx` page
- MCP actions `list/get/create/update/delete_strategy`, `strategy_id` params on
  `manage_routines` / `manage_skill` / `start_agent`

## 3. Target schema

```
agents/{slug}/
    AGENT.md                # identity + domain knowledge + tick playbook (one body)
    config.yml              # runtime config (was strategy-level)
    learnings.md            # cross-session learnings (agent-level, all run kinds)
    shutdown.md             # optional winddown override (walk: agent → _defaults)
    skills/  routines/  store/                    # unchanged
    sessions/
        session_N/
            meta.yml        # kind, status, task, timestamps, model — see §5
            journal.md      # tick-loop sessions only
            config.yml      # frozen copy of launch config (tick-loop only, as today)
            snapshots/snapshot_T.md   # tick-loop sessions only
            transcript.md   # delegation (and consult) sessions
    dry_runs/experiment_N.md          # unchanged format, moved up one level
agents/_defaults/shutdown.md          # unchanged
```

### 3.1 AGENT.md — merged frontmatter

```yaml
---
name: Market Making Expert
description: ...
agent_key: claude-acp:sonnet        # single model default (config.yml can override)
tools: [...]
when_to_consult: ...                # non-empty ⇒ consultable (unchanged)
server_required: true
server_name: moneymaker
loopable: true                      # NEW — explicit opt-in to the tick loop
default_config: {frequency_sec: 120, total_amount_quote: 500, risk_limits: {...}}
default_trading_context: ""
launch_presets:                     # NEW (optional) — named launch overlays
  jto: {trading_context: "Do MM on JTO-USDT on binance_perpetual", total_amount_quote: 300}
  sol-tight: {trading_context: "Do MM on SOL-USDT on binance_perpetual",
              risk_limits: {max_open_executors: 6}}
created_by: ...
created_at: ...
---
<identity + domain knowledge>

## Tick Playbook
<what to do each tick — the former strategy.md body>
```

Decisions baked into this shape:

- **Single body, no parsing.** Both the consult prompt (`build_agent_context`)
  and the tick prompt (`build_tick_prompt`) receive the full body. The
  `[AGENT — domain identity]` / `[STRATEGY INSTRUCTIONS]` split in
  `prompts.py` collapses to one `[AGENT]` section. The `## Tick Playbook`
  heading is a human convention, not machine-parsed. Consequence: consults now
  also see the tick playbook — acceptable (arguably useful context), and the
  simplicity is the point.
- **`loopable: true` is explicit, not derived.** Capability was previously
  derived from "owns ≥ 1 strategy"; with strategies gone the honest signal is a
  flag. Per the no-silent-fallbacks rule, `start_agent` on a non-loopable agent
  returns a clear error rather than looping an agent that has no playbook.
  (`consultable` stays derived from `when_to_consult`, unchanged.)
- **Model override chain shrinks to `config.agent_key > agent.agent_key`.**
  The strategy-level override disappears. Neither on-disk strategy sets
  `agent_key` (both are `null`), so nothing is lost in migration; the script
  still errors loudly if it ever encounters a strategy `agent_key` that
  conflicts with the agent's.
- **Dropped fields:** `Strategy.skills` (vestigial, see §1). `Strategy.name`/
  `description`/`created_*` are superseded by the agent's own.
- **`launch_presets` replace "second strategy as saved preset."** Under the
  two-tier model, the only way to *persist* a per-market setup (same playbook,
  different capital/context/caps) was to clone the strategy. Post-merge, a
  preset is a named overlay merged over `default_config` at launch —
  `start_agent(agent_slug=…, preset="jto")`; an explicit `config` param still
  wins over the preset. Unknown preset name ⇒ loud error, never a silent
  fall-through to defaults. This keeps the multi-instance workflow (§8)
  launchable by name instead of by retyping config, without resurrecting the
  strategy tier.

### 3.2 Identity keys

| Today | After |
|---|---|
| strategy key `mm_expert.pmm_mister_operator` | just the agent slug |
| session id `mm_expert.pmm_mister_operator_3` | `mm_expert_3` |
| experiment id `...e2` | `mm_expert_e2` |
| delegate id `routine_builder-delegate-ae0f1c21` | `routine_builder_7` (a session number; the `-delegate-` uuid remains only as the ephemeral in-memory registry handle, recorded in `meta.yml`) |

`agent_id` remains the `controller_id` tag on executors, so PnL attribution
keeps working with zero changes to `performance.py`. Because no session history
exists on disk, no historical `controller_id` with a dot needs to resolve —
**delete** `split_key`, the dot-splitting in `agent_strategy_from_agent_id`,
and the dual-format branch in `journal.resolve_agent_dirs` /
`journal._strategy_base_dir` rather than keeping compatibility parsing.

Naming note: `agent_id` now reads ambiguously against `agent_slug`
(`mm_expert_3` vs `mm_expert`). Recommend renaming the run identifier to
`session_id` throughout while every call site is already being touched; if that
is too much churn for one PR, keep `agent_id` and rename in a follow-up — but
do not do it halfway.

## 4. Sessions as the uniform run envelope

The core new idea: **a session is one run of the agent, of any kind.**

```yaml
# sessions/session_7/meta.yml
kind: delegation            # tick_loop | delegation | consult
status: done                # running | done | error | stopped   (tick_loop: running|stopped|shutdown)
task: "Create a routine that ..."     # delegation/consult: the task text
task_id: routine_builder-delegate-ae0f1c21   # delegation only: registry handle
model: claude-code:sonnet
server: moneymaker
started_at: 2026-07-11T18:02:11Z
ended_at: 2026-07-11T18:06:40Z
tool_calls: 14
```

Per kind:

- **tick_loop** — exactly today's session dir: `journal.md`, frozen
  `config.yml`, `snapshots/snapshot_T.md`. Only tick sessions have a journal;
  the journal protocol in prompts is unchanged.
- **delegation** — `meta.yml` + `transcript.md` (the current
  `_persist_transcript` rendering: task, chronological thoughts/tool
  calls/text, result/error). `delegate._run()` allocates the session dir at
  start (so a crash still leaves a `status: running` husk to inspect) and
  finalizes meta on completion. The `agents/{slug}/delegations/` directory is
  retired; the migration script converts the 4 existing transcripts.
- **consult** — same shape as delegation. See §10 (recommended in, flagged as
  the one genuine scope decision).

This directly delivers the ask: `routine_builder` (and any consultable/
delegatable agent) accumulates numbered, analyzable sessions with full
snapshots of what it did — and because `learnings.md` now lives at the agent
level, a delegation can append learnings (e.g. routine-authoring pitfalls)
that future delegations see. Today that feedback loop only exists for tick
loops; extending `_run_agent_to_completion`'s prompt with the agent's
learnings + a `journal_write(learning)` affordance is a small, high-value
addition once the storage is unified (can land as a fast-follow).

### Session numbering under concurrency

Today only `TickEngine.__post_init__` allocates session numbers. After this
change, delegations (and consults) allocate from the same per-agent counter,
potentially interleaved across awaits on the single asyncio loop, and dry-run
experiments share the numbering *space* (`_eN` suffix keeps them distinct).
Replace scan-and-return `next_session_number` with **allocate-by-mkdir**:

```python
def allocate_session_dir(agent_dir: Path) -> tuple[int, Path]:
    n = scan_max(agent_dir / "sessions") + 1
    while True:
        d = agent_dir / "sessions" / f"session_{n}"
        try:
            d.mkdir(parents=True, exist_ok=False)
            return n, d
        except FileExistsError:
            n += 1
```

Atomic at the filesystem level, no locks, works even if a second process ever
shares the data dir.

### What stays out of sessions (for now)

`dry_runs/` keeps its flat `experiment_N.md` format, just moved up to the
agent dir — **by decision, not deferral**: dry runs are deliberately isolated
scratch runs for building/testing an agent without touching its journal,
learnings, or session track record. Folding them into `sessions/` was
explored and rejected — see [refactor-03](refactor-03-dry-run-flag.md)
(withdrawn) for the analysis. Same for `MAX` retention policies on
delegation/consult sessions (tick snapshots already cap at 100; add a cap when
consult persistence lands).

### …but `run_once` moves to the sessions side

Refactor-03's salvaged micro-improvement #2, promoted to in-scope here:
`execution_mode: run_once` trades **live** (real executors, real PnL) yet is
stored today as an experiment — flat file, **no journal**, `_eN` id. With the
boundary now stated as *dry_runs = scratch that never touches capital or the
agent; sessions = anything that does*, run_once is on the wrong side.

Fix (a config mapping, not a new mode): the engine maps `run_once` to an
**ordinary tick session with `max_ticks: 1`** — `is_experiment` narrows to
`execution_mode == "dry_run"` alone. A run_once run then gets everything a
live run deserves: a journal, a frozen config, real risk pre-flight
(`is_blocked`/`should_shutdown` no longer skipped — it's live capital), `_N`
`controller_id` attribution, and a place in the track record. The existing
`max_ticks` self-stop machinery (`engine.py:305-314`) already handles the
single-tick lifecycle and completion notification. The `_eN` namespace is
left to true dry runs only.

This also keeps the run-kind boundary crisp once refactor-02 collapses the
execution stacks: **tick (any duration) = standing playbook + injected market
state + journal + attribution + track record; delegation = ad-hoc task +
transcript, unattributed** — without this fix the two nearly converge (both
single background runs under a zero-seeded risk gate) and differ mainly in
storage, backwards.

## 5. Code change inventory

### Deleted
| File | Notes |
|---|---|
| `condor/agents/strategy.py` | `Strategy`, `StrategyStore`, `split_key` gone. `_slugify`, `_parse_frontmatter`, `_render_frontmatter` move to `agent.py` (they're generic). |

### Core model
| File | Change |
|---|---|
| `condor/agents/agent.py` | `Agent` gains `loopable: bool`, `default_config: dict`, `default_trading_context: str`. Remove the now-dead `d.name == "strategies"` guard in `_iter_agent_dirs`. `delete()` semantics: refuse while an engine is running (mirror the web route), keep the "unlink AGENT.md only, preserve non-empty dir" behavior so history is never silently destroyed. |
| `condor/agents/engine.py` | Constructor takes `agent` only (drop `strategy`). `strategy_dir` → `agent.agent_dir`; run key → bare slug; `_agent_key()` drops the strategy tier; `_build_routines_section` and `get_info` lose strategy fields; session allocation via `allocate_session_dir` + write `meta.yml` (`kind: tick_loop`). Gate `start` on `agent.loopable`. `is_experiment` narrows to `execution_mode == "dry_run"`; `run_once` maps to a normal session with `max_ticks: 1` (§4). |
| `condor/agents/journal.py` | `resolve_agent_dirs`/`_strategy_base_dir`: single format `{slug}_{N}` / `{slug}_eN` → `agents/{slug}/…`; delete the dot branch. Rename the misleading `agent_dir` param docs (it *was* the strategy dir; now it genuinely is the agent dir). **Strip legacy read paths**: `trading_sessions/`, `runs/`, `experiments/`, "Active Insights"/journal-embedded learnings fallbacks — the migration script normalizes disk once so the code never reads legacy layouts again. |
| `condor/agents/sessions_index.py` | Takes the agent dir. Drop legacy dirname tuples. Extend `list_sessions`/`enumerate_agent_ids` to read `meta.yml` and return `kind`, so consumers can filter (perf rollups only fetch executor data for `tick_loop` sessions — delegation/consult sessions have no executors and must not generate backend queries). |
| `condor/agents/prompts.py` | `build_tick_prompt(agent, config, …)`; single `[AGENT]` section from the merged body; `_build_routines_section` keyed by slug; the routine-call example uses `agent_slug=` instead of `strategy_id=`. |
| `condor/agents/shutdown.py` | Policy walk becomes `agents/{slug}/shutdown.md → agents/_defaults/shutdown.md`. Signature takes `Agent`. |
| `condor/agents/config.py` | Unchanged logic; callers pass the agent dir. |
| `condor/agents/consult.py` | Unchanged for the run itself; optionally allocates a `kind: consult` session (§10). |
| `condor/agents/delegate.py` | `_persist_transcript` → session dir + `meta.yml` (allocate at start, finalize in `finally`). Registry/`task_id`/notify flow unchanged. |
| `condor/agents/performance.py` | No changes (id-driven). |

### MCP server (`mcp_servers/condor/`)
| File | Change |
|---|---|
| `tools/trading_agent.py` | Delete `_manage_strategy` and the 5 strategy actions. `create_agent`/`update_agent` accept `loopable`, `default_config` (as `config`), `default_trading_context`, `launch_presets`. `start_agent` takes `agent_slug` and optional `preset` (§3.1); lifecycle URLs become `/agents/{slug}/start|stop|pause|resume|shutdown`. `_list_agent_definitions` reports `loopable` from the flag. Journal/monitoring resolve via the new id format. Add a `list_sessions` action (kind-aware) so chat can enumerate any agent's history, including delegations. |
| `tools/routines.py`, `tools/skills.py` | `strategy_id` param → `agent_slug` (the bare-slug fallback already in `_get_agent_routines_dir` becomes the *only* behavior; delete the composite-key branch). No dual-accept: tool schemas are re-injected per session, and in-flight sessions breaking on restart is acceptable. |
| `condor_client.py` | `runkey_from_agent_id` → `slug_from_agent_id` (strip trailing `_N`/`_eN`); delete `agent_strategy_from_agent_id` and the alias. |
| `server.py` | Rewrite `manage_trading_agent` / `manage_routines` / `manage_skill` docstrings (this is the LLM-facing API — treat it as a first-class deliverable, not doc polish). |

### Web API + frontend
| File | Change |
|---|---|
| `condor/web/routes/agents.py` | Flatten: `/agents/{slug}/{start,stop,pause,resume,shutdown,performance,learnings,config,sessions,sessions/{n}/journal,…,experiments,routines,reports}`. `StrategySummary` merges into `AgentSummary` (drop `_aggregate_strategy_perf` — nothing to aggregate). `SessionInfo` gains `kind`/`status`/`task`. Keep `/delegations` registered before `/{slug}` (unchanged ordering constraint). Report attribution prefix becomes `{slug}/` (see §8). |
| `frontend/src/pages/StrategyDetail.tsx` | Merged into `AgentDetail.tsx` (tabs: Overview / Sessions / Dry runs / Learnings / Config). Route `/agents/:slug/strategies/:sslug` removed. `Agents.tsx` cards flatten. `api.ts` types + ~20 endpoint helpers collapse. Sessions list shows kind badges (tick / delegation / consult). |

### Docs, skills, tests
- `assistants/condor/skills/agent_builder/SKILL.md` — significant rewrite: the
  create → consult → routines → *optional strategy* progression becomes
  create → consult → routines → *optional tick playbook* (set `loopable`,
  write the `## Tick Playbook` section, `start_agent(agent_slug=…)`).
- `agents/routine_builder/AGENT.md` — its "Global vs Agent-Local Routines"
  section and MCP examples teach `strategy_id="agent_slug.strategy_slug"`
  (6 references); post-merge the agent would pass a dead parameter on every
  agent-local request. Rewrite to `agent_slug="<slug>"` and drop the
  "ask for the strategy_id" step. **General migration checklist item:** grep
  every agent body and skill for `strategy_id` / `strategies/` — prose
  prompts are invisible to the script and the type checker, and a stale
  instruction makes the agent *actively* call the API wrong.
- `docs/architecture/agent-framework.md`, `strategy-engine-and-shared-intelligence.md`
  — update §4/strategy references; the tokenizable-unit argument in
  `docs/strategy/business-strategy.md` §11a transfers cleanly: the *agent* is
  now the unit with isolated config, sessions, and track record.
- `tests/test_agents.py` — rewrite `test_strategy_crud_under_agent`,
  `test_strategy_agent_key_override_optional`,
  `test_create_strategy_requires_existing_agent` as agent-level equivalents;
  add tests for `loopable` gating, session allocation under interleaving,
  delegation session persistence, and the migration script itself.
  `test_reports_attribution.py` — update run-key expectations.

## 6. Migration (one-shot script, no legacy readers)

`scripts/migrate_agent_strategy_merge.py`, run once with the backend stopped.
Per the no-fallbacks rule, code after this refactor reads only the new layout;
the script is the single place legacy formats are understood.

1. **Preflight (fail loudly, change nothing):**
   - **back up `agents/` first** (copy to `agents.pre-merge.bak/`, or commit
     it): `agents/` is untracked in git, and post-migration code has **no
     legacy readers** — without a backup, a botched run is unrecoverable;
   - refuse if any `TickEngine` could be live (require the process stopped);
   - refuse if any agent has **> 1 strategy** (manual split into two agents —
     none exist today);
   - refuse on `strategy.agent_key` conflicting with the agent's (none today);
   - refuse on unexpected files in `strategies/{sslug}/`.
2. **Merge AGENT.md:** copy `loopable: true`, `default_config`,
   `default_trading_context` (drop `skills`, strategy `name`/`description`/
   `created_*`); append the strategy body under `\n\n## Tick Playbook\n`.
   Agents with no strategy get no new frontmatter (absent `loopable` ⇒ false).
   **Editorial follow-up for mm_expert:** the concatenation puts its two
   *conflicting* pmm_mister parameter guides in one file (AGENT.md guide says
   `total_amount_quote` default 1000 / leverage "1–5x"; strategy.md schema
   says 100 / "default 20") — dedupe into one section, verifying defaults
   against `manage_controllers(action="describe", controller_name=
   "pmm_mister")` rather than trusting either copy. (This was refactor-04's
   §2.2/§6 reconciliation step; with r04 tabled it belongs here.)
3. **Move files up:** `config.yml`, `learnings.md`, `shutdown.md`, `dry_runs/`,
   `sessions/` from the strategy dir to the agent dir (error on collision —
   today none of these exist at agent level); remove the empty `strategies/` tree.
4. **Convert delegations:** each `delegations/{task_id}.md` becomes
   `sessions/session_N/` with `transcript.md` (content as-is) and a `meta.yml`
   reconstructed from the transcript header (`kind: delegation`, status, task,
   `task_id`); remove `delegations/`.
5. **Backfill `meta.yml`** (`kind: tick_loop`) into any migrated tick session
   dirs — none exist today, but the script handles it for other checkouts.
6. Print a diff-style summary of every move/rewrite.

Data inventoried on this machine: 2 strategy.md files, 5 experiments,
4 delegation transcripts, 0 sessions, 0 strategy-level `config.yml`/
`learnings.md`/`shutdown.md`. The script's real work is ~6 file moves and
2 AGENT.md rewrites.

## 7. Tradeoffs

**What we give up**

- *Several playbooks sharing one brain.* The old model let
  `market_making_expert` run `pmm_mister_operator` on one pair and a second
  playbook on another, sharing skills/routines/memory. Post-merge, a second
  playbook means a second agent, and because skills/routines are siloed per
  agent (no global skills tier yet), its domain knowledge must be **copied**.
  This is the one real capability loss. Mitigations: (a) it is unused today —
  every agent has ≤1 strategy; (b) parameterization already covers the common
  case (pmm_mister_operator takes pair/connector from launch config, so "same
  playbook, different market" is one agent with different session configs, now
  persistable as named `launch_presets`, §3.1);
  (c) the systemic fix is a shared-skills tier, designed in
  [refactor-04](refactor-04-shared-skills-tier.md) but **tabled** — it is
  purely additive and can land whenever a second same-domain agent makes the
  copying real. Until then this loss is accepted.
- *Per-strategy model override.* Chain shrinks to config > agent. Unused.
- *Per-strategy shutdown override.* Walk shrinks to agent → default. Unused.

**What we gain**

- One entity, one directory, one identity per autonomous trader — the agent
  *is* the strategy, with its own config, journal, learnings, and track record
  (which also makes it the clean tokenizable/mandatable unit).
- Uniform "every run is a session" history for all agents, including pure
  specialists like `routine_builder` — the prerequisite for cross-run
  learning loops and for any future analysis tooling.
- Deletion of the composite-key plumbing (`split_key`, run-key parsing, dot
  conventions), the strategy CRUD surface (MCP + web + frontend page), the
  legacy read paths in `journal.py`/`sessions_index.py`, and one level of both
  the REST route tree and the UI. Net LOC is solidly negative.
- A materially simpler mental model to explain in `agent_builder` and the
  system prompt — fewer routing concepts for the coordinating LLM to misapply.

## 8. Edge cases & ramifications

- **Delegation while the tick loop runs.** Both allocate sessions concurrently
  → handled by mkdir-allocation (§4). Delegations never touch `journal.md`
  (only tick sessions have one), so no write contention.
- **Concurrent tick sessions of one agent are supported today and preserved.**
  Neither start path has an already-running guard (web `agents.py:1076`, MCP
  `start_agent` both construct a fresh `TickEngine` unconditionally) and the
  UI already models `instances: list[RunningInstance]` — "same playbook,
  three markets" is three launches (now: three presets). Every isolation that
  makes this work survives the merge: per-session registry entry, journal,
  frozen config, risk caps, `controller_id`. Two consequences to know:
  (a) *learnings mixing* — all instances share the agent-level `learnings.md`,
  so pair-specific learnings surface in every market's ticks; the playbook
  should prefix market-specific learnings with the pair, and isolation can be
  revisited if the noise becomes real. (b) *no aggregate exposure cap* — risk
  caps are per-session, so N instances can hold N× the position cap. This is
  pre-existing (identical under the strategy tier), but the merged agent is
  now the natural home for a future agent-level ceiling.
- **Perf rollups must skip non-tick sessions.** `enumerate_agent_ids` feeding
  `fetch_agent_performance_batch` would otherwise issue pointless backend
  queries for delegation/consult session ids (worse: a delegation session id
  like `mm_expert_7` *could* collide with executor tags if a tick session ever
  reused the number — the shared counter prevents reuse, but filter by
  `kind == tick_loop` regardless).
- **Report attribution prefix changes.** `get_strategy_reports` filters
  `source_name.startswith("{slug}.{sslug}/")`; routines run by future sessions
  will report under `{slug}/`. Existing reports keep old source names — the
  migration script should also rewrite report `source_name` records (they live
  in the reports store), or the plan accepts that pre-migration reports stop
  matching the agent filter. Recommend: rewrite in the script (it's a string
  prefix swap) to honor the no-orphaned-data principle.
- **In-flight chat sessions break at deploy.** MCP tool schemas and Condor's
  injected `[AGENTS]`/skill indexes are snapshotted per session; sessions
  started before the deploy will call removed actions (`create_strategy`) and
  get clear "unknown action" errors. Acceptable — restart sessions after
  deploying (context is rebuilt per session anyway).
- **`AgentStore.delete` + history.** Deleting an agent now implicitly ends its
  (single) strategy. Keep the current conservative behavior — unlink AGENT.md,
  never rm a non-empty dir — and drop the "delete its strategies first" guard
  in favor of "refuse while running". Surfacing an explicit archive/purge flow
  is out of scope.
- **`when_to_consult` + `loopable` interplay.** All four current agents remain
  consultable; two become loopable. A loopable-but-not-consultable agent is
  still valid (loop-only trader). Both false ⇒ same "stub" state as today.
- **Slug collision space.** Agent slugs and old strategy slugs never merge into
  one namespace (strategies keep no identity), so no collision handling needed.
- **`memory/paths.py`** — comment-only updates; the assistants/agents split and
  per-agent stores are untouched by this refactor.
- **Other checkouts / deployments** with real session history: the migration
  script moves `sessions/` intact, but historical executor tags
  (`{slug}.{sslug}_{N}` as `controller_id` in the Hummingbot backend) will no
  longer be enumerated after the id format change — per-session PnL for
  pre-migration sessions is lost from the dashboard (raw data remains in the
  backend DB). On this machine: zero impact. Call it out in the PR description.

## 9. Implementation sequence

Reviewable increments, each leaving the tree green:

1. **Core model** — `agent.py` absorbs strategy fields; delete `strategy.py`;
   update `engine.py`, `journal.py` (incl. legacy-path strip),
   `sessions_index.py`, `prompts.py`, `shutdown.py`, `condor_client.py`;
   migration script + preflight; run migration on the working tree; update
   `tests/test_agents.py`.
2. **Session unification** — `meta.yml` + `allocate_session_dir`; delegate
   persistence → sessions; kind-aware `sessions_index`; (optional) consult
   persistence.
3. **MCP surface** — `trading_agent.py`, `routines.py`, `skills.py`,
   `server.py` docstrings, `agent_builder` SKILL rewrite.
4. **Web + frontend** — flatten routes, merge `StrategyDetail` into
   `AgentDetail`, `api.ts`.
5. **Docs** — `agent-framework.md`, strategy-engine doc cross-references.

Steps 1–2 are the substance; 3–5 are mechanical fan-out. Suggest one PR per
step onto this spike branch.

## 10. Open decisions (recommendations inline)

1. **Persist consults as sessions?** *Recommend yes, in step 2* — it completes
   the "invocable ⇒ analyzable" principle and reuses delegate's `event_sink`
   verbatim; add a simple retention cap (e.g. keep last 50 consult sessions per
   agent) since consults are frequent. If deferred, the `meta.yml` `kind` field
   ships anyway so adding it later is additive.
2. **Rename `agent_id` → `session_id`?** *Recommend yes* while every call site
   is open (engine, journal tools, performance, web models, frontend types);
   defer only if step 1's diff gets unwieldy — then do it as its own mechanical
   PR immediately after.
3. **Give delegations journal/learnings access in their prompt?** *Recommend
   as a fast-follow after step 2* — inject agent-level `learnings.md` into
   `_run_agent_to_completion`'s context and allow
   `journal_write(entry_type="learning")` against the agent (no tick journal
   needed). Small change, closes the learning loop for `routine_builder`.
4. **Fold `dry_runs/` into sessions?** Resolved: **no** — dry runs stay a
   separate flat artifact by design (isolated build/test scratch space that
   never touches the track record). See
   [refactor-03](refactor-03-dry-run-flag.md) (withdrawn) for the analysis.
   Of its two salvageable micro-improvements, the `run_once` wrinkle is now
   resolved in scope here (§4 — run_once becomes a `max_ticks: 1` session);
   frontmatter-over-regex in `EXPERIMENT_TEMPLATE` remains optional polish.

# Refactor 01b — Agent-level history, strategies kept as playbooks

Status: **implemented** (2026-07-11; commits 2ce7760, dc76d58 — full §9 sequence incl. refactor-02), superseding
[refactor-01](refactor-01-agent-strategy-merge.md) (tabled)
· Branch: `spike/simpler-agent-framework`

## 1. The reframe

Refactor-01 bundles two separable ideas:

- **(A) Unify run history at the agent level** — one `sessions/` envelope for
  tick/delegation/consult, agent-level `learnings.md` and `dry_runs/`, simple
  `{slug}_{N}` ids, delegations become sessions.
- **(B) Merge strategy into agent** — one playbook per agent, `strategy.md`
  folded into AGENT.md, strategy CRUD deleted.

The user-capability review showed (B) carries the only losses that matter:
**A/B-testing playbook variants under one agent** (loss #1, unmitigated) and
multi-playbook agents sharing one brain (partially deferred to the tabled
refactor-04). Meanwhile (A) carries nearly all the wins: uniform sessions,
analyzable delegations, id simplification, legacy-path deletion.

**Refactor-01b is (A) without (B).** Strategies survive — demoted from
*state-owning subdirectories* to *pure playbook templates*. All operational
state centralizes at the agent; which playbook a session ran becomes metadata.

## 2. Target schema

```
agents/{slug}/
    AGENT.md                    # identity + domain knowledge (role unchanged)
    learnings.md                # agent-level — all strategies, all run kinds
    shutdown.md                 # agent-level override (walk: agent → _defaults)
    skills/  routines/  store/  # unchanged
    sessions/
        session_N/
            meta.yml            # kind + strategy + status/task/model/timestamps
            journal.md          # tick sessions
            config.yml          # frozen launch config (tick)
            snapshots/          # tick
            transcript.md       # delegation / consult
    dry_runs/experiment_N.md    # agent-level; frontmatter records strategy+mode
    strategies/{sslug}/
        strategy.md             # playbook body + default_config frontmatter — and
                                # NOTHING else: no sessions/, learnings.md,
                                # dry_runs/, config.yml, shutdown.md below here
```

```yaml
# sessions/session_N/meta.yml — one field more than refactor-01's
kind: tick_loop               # tick_loop | delegation | consult
strategy: pmm_mister_operator # tick sessions only; absent for delegation/consult
status: stopped
...
```

Since a strategy directory now holds a single file, optionally flatten to
`playbooks/{sslug}.md` and rename the concept to match what it now is — a
playbook, not a state-owning entity. (Recommended, but cosmetic; everything
below works either way.)

## 3. Identity — same simplification as refactor-01

This is the key observation: **the strategy slug can leave the identity layer
even though strategies stay.** Sessions are numbered per-agent, so:

| Today | 01b (same as 01) |
|---|---|
| `mm_expert.pmm_mister_operator_3` | `mm_expert_3` (meta.yml: `strategy: pmm_mister_operator`) |
| `mm_expert.pmm_mister_operator_e2` | `mm_expert_e2` |
| `routine_builder-delegate-ae0f1c21` | `routine_builder_7` |

Everything refactor-01 §3.2 deletes still gets deleted: `split_key`, the
dot-parsing in `agent_strategy_from_agent_id`, the dual-format branches in
`journal.resolve_agent_dirs`, the legacy read paths. `controller_id`
resolution becomes `mm_expert_7 → agents/mm_expert/sessions/session_7`,
identical to 01. The strategy is a **start-time selector plus session
metadata**, not an address:

```
start_agent(agent_slug="mm_expert", strategy="pmm_mister_operator", config={...})
    strategy optional when the agent has exactly one; REQUIRED (loud error
    listing options) when it has several; error when it has none.
```

Two triads that refactor-01 deleted get decided separately here:

- **Model override `config > strategy > agent`: keep.** With A/B testing as a
  first-class goal, "variant B on the cheaper model" is a real use.
- **Shutdown walk `strategy → agent → _defaults`: drop the strategy tier.**
  Winddown is session-scoped and positions don't care which playbook opened
  them; nothing on disk uses it.

## 4. What each concern looks like under 01b

- **A/B playbook variants (refactor-01's loss #1): solved.** Create
  `pmm_v2` beside `pmm_mister_operator`; run both; sessions interleave in one
  agent-level list, each tagged `strategy:` in meta. Per-playbook track
  records are a metadata filter (`sessions where strategy == pmm_v2`), and the
  UI can show a strategy filter chip instead of a separate page tree. This is
  strictly better than today's version of the capability — the variants'
  sessions are directly comparable in one list.
- **Multi-playbook agents sharing one brain: retained.** Skills, routines,
  memory, learnings stay shared at the agent. This also removes most of the
  pressure that motivated refactor-04 (tabled): a second same-domain playbook
  no longer requires a second agent at all.
- **Learnings isolation: deliberately given up** — this alternate *chooses*
  the agent-level pool (that's the point of A). Mitigation, cheap and worth
  shipping with it: `append_learning` prefixes entries from tick sessions
  with their strategy slug (`[pmm_v2] JTO book is thin…`), mirroring the
  `[dry]` provenance idea from refactor-03 — mixing stays visible and
  filterable. Delegation/consult learnings (once enabled) carry no prefix.
- **Persistent per-market setups:** refactor-01's `launch_presets` become
  unnecessary in their original role — a second strategy *is* a persistent
  named setup, and a richer one (it can vary the body, not just the config).
  Presets can still be added later per-strategy if config-only variants
  proliferate; not part of 01b.
- **`loopable` flag: not needed.** Derivation stays what it is today —
  an agent with ≥1 strategy can loop. No new frontmatter.
- **`run_once` moves to the sessions side (carried from refactor-01 §4).**
  The engine maps `execution_mode: run_once` to an ordinary tick session with
  `max_ticks: 1`; `is_experiment` narrows to `dry_run` alone. A run_once run
  gets a journal, frozen config, real risk pre-flight, `_N` attribution, and
  a place in the track record; `_eN` is reserved for true dry runs. Boundary:
  *dry_runs = scratch that never touches capital; sessions = anything that
  does* — which also keeps tick vs delegation crisp under refactor-02.
- **Concurrent instances (multi-controller): unchanged** from the 01 analysis
  — no start guard, per-session isolation, agent-level session counter with
  mkdir-allocation handles two strategies (or two markets) starting at once.
  The aggregate-exposure note from 01 §8 applies identically.

## 5. Delegation limits, loosened (amends refactor-02 §4.1)

Adopted into refactor-02 (see its §4.1/§9): the zero-seeded `risk_gate`
default for trading delegations stays, **with a per-delegation override**:

```
delegate(action="start", agent="mm_expert",
         task="deploy on BP-USDC with up to 2000",
         risk_limits={"max_position_size_quote": 2000,
                      "max_open_executors": 20})
```

- The human issuing the call is the authorizer — an explicit per-call
  `risk_limits` dict **replaces** the agent's baseline for that run (replace,
  not merge: what you pass is exactly what governs).
- Under 01b, the agent-level baseline needs a home that isn't a strategy
  (delegations aren't strategy-scoped): AGENT.md frontmatter gains
  `risk_limits:` — the delegation baseline, and the fallback a strategy's
  `default_config` can override for its own tick sessions.
- No `policy="auto"` escape for trading agents: "unbounded" is expressed by
  passing explicitly large caps — the number must be said out loud. The loud
  error now fires only when a trading delegation has **neither** an agent
  baseline **nor** a per-call override.
- The `pmm_mister_deploy` Step-6 migration item (deploy must declare
  `max_global_drawdown_quote`) is unchanged — the gate still requires bounded
  deploys regardless of which limits are in force.

## 6. Code change inventory (delta vs refactor-01's)

Same as refactor-01 for: `journal.py` (single id format + legacy strip),
`sessions_index.py` (agent dir, meta-aware — plus surfacing `strategy`),
delegate/consult session persistence, `allocate_session_dir`, migration of
delegation transcripts, perf-rollup kind filtering, report attribution.

Differs:

| Area | Refactor-01 | Refactor-01b |
|---|---|---|
| `strategy.py` | deleted | kept, **slimmed**: `Strategy` = name/description/body/`default_config`/`agent_key`; drop `skills` (vestigial) and every dir-resolution helper (`sessions_dir` etc.) |
| `engine.py` | takes `agent` only | keeps `(agent, strategy)`; session dir + journal + ids resolve via **agent**; `meta.yml` gains `strategy:` |
| Strategy CRUD (MCP + web) | deleted | kept; `create/update_strategy` lose nothing, `list_strategies` reads playbooks only |
| `prompts.py` | single `[AGENT]` section | keeps `[AGENT]`/`[STRATEGY INSTRUCTIONS]` split |
| Frontend | `StrategyDetail` merged away | `StrategyDetail` slims to a playbook editor; sessions/experiments/learnings tabs move to `AgentDetail` with a strategy filter |
| AGENT.md frontmatter | `loopable`, `default_config`, `default_trading_context`, `launch_presets` | `risk_limits` only (delegation baseline, §5) |
| `agent_builder` SKILL | "optional tick playbook" flow | keeps the create-strategy step; updated for `agent_slug` params and agent-level history |

The `routine_builder` AGENT.md prose fix and the `strategy_id → agent_slug`
param rename apply the same way (`manage_routines`/`manage_skill` scope to an
*agent*, not a strategy — that was already true in behavior, since skills and
routines never lived under strategies).

Migration: identical script skeleton (backup first — same untracked-`agents/`
warning), moving `sessions/`/`learnings.md`/`dry_runs/` **up** instead of
merging AGENT.md; with ≤1 strategy per agent today the learnings move is a
plain rename (the multi-strategy concat branch exists but is unexercised).
Delegation transcripts convert as in refactor-01 §6.4, with `ended_at` taken
from file mtime and `started_at` omitted (the flat header has no timestamps —
record what's known, fabricate nothing; readers must tolerate the absent
field anyway for crashed-husk sessions).

**Editorial checklist item (no forcing step under 01b):** the drifted
pmm_mister parameter knowledge stays split across two files that 01b does
*not* merge (mm_expert's AGENT.md guide vs strategy.md schema —
`total_amount_quote` 1000 vs 100, leverage "1–5x" vs "default 20"). Reconcile
both against `manage_controllers(action="describe")` during the migration
pass; under 01b nothing else will ever force this.

Refactor-01 §10's open decisions carry over unchanged: consult persistence
(*recommend yes, step 2*), `agent_id` → `session_id` rename (*recommend yes*),
delegation learnings access (*fast-follow*); §10.4 is resolved (dry runs stay;
run_once adopted, §4).

## 7. Tradeoffs vs refactor-01

**01b gives up (relative to 01):**

- Less deletion. The strategy surface survives: `strategy.py` (slim),
  5 MCP actions, strategy web routes, a (smaller) `StrategyDetail`, the
  two-section prompt split. Net LOC still negative (identity plumbing +
  legacy paths + per-strategy state resolution all go), but much less so.
- Two-level mental model persists for the coordinating LLM and the
  `agent_builder` flow: create agent → (optionally) create strategy → start.
  This was refactor-01's "materially simpler to explain" gain, halved.
- The agent is no longer literally "the tokenizable unit with one track
  record" — its track record is now the union of its playbooks' sessions
  (per-playbook views need the metadata filter).

**01b keeps that 01 loses:**

- A/B playbook testing under one agent (the review's #1 loss) — improved,
  even, by the unified comparable session list.
- Multi-playbook agents sharing skills/routines/memory — no copied knowledge,
  no dependence on the tabled refactor-04.
- Per-playbook model override (now with a real use case).

**Shared by both (unchanged from the review):** agent-level learnings mixing
(mitigated by strategy-prefix provenance here), run_once moving on-record,
delegation caps by default (now with the §5 override), id/path breakage at
migration, in-flight chat sessions erroring on renamed params.

**Compatibility with refactor-02: full.** r02 needs the unified session
envelope and a per-run persistence hook — 01b provides the same envelope at
the same paths. The only r02 touch-point that changes is where the delegation
risk baseline lives (AGENT.md `risk_limits` instead of
`default_config.risk_limits`).

## 8. Decision framing — when to pick which

Pick **01** if the priority is the smallest possible system: one entity, one
playbook, maximum deletion — and playbook A/B testing is acceptable as
"clone the agent" (which wants refactor-04 un-tabled eventually).

Pick **01b** if playbook iteration with comparable track records under one
brain is a real workflow (the mm_expert power-user pattern), at the price of
keeping a slimmed strategy tier. It extracts ~all of refactor-01's
operational wins (sessions, ids, delegations, legacy deletion) while touching
none of its capability losses except learnings pooling — which both variants
choose deliberately.

They are mutually exclusive as written, but 01b → 01 remains cheap later: if
multi-strategy stays unused after 01b, collapsing a pure-template playbook
into AGENT.md is a small editorial migration (the state is already
agent-level — exactly the hard part 01's migration script exists for).

## 9. Implementation sequence

Reviewable increments, each leaving the tree green; refactor-02 follows per
its own §8 once steps 1–2 land.

1. **Core identity & storage.** `journal.py`: single `{slug}_{N}`/`{slug}_eN`
   → `agents/{slug}/…` resolution, delete dot-branches and legacy read paths
   (`trading_sessions/`, `runs/`, `experiments/`, journal-embedded learnings).
   `allocate_session_dir` (mkdir-atomic, per refactor-01 §4). `engine.py`:
   session dir/journal/ids resolve via the agent dir; write `meta.yml`
   (`kind: tick_loop`, `strategy:`); `run_once` → `max_ticks: 1` session,
   `is_experiment` narrows to `dry_run`. Slim `strategy.py` (drop `skills` +
   all dir-resolution helpers). `shutdown.py`: walk `agent → _defaults`.
   `condor_client.py`: `slug_from_agent_id`, delete composite parsing.
   Migration script (backup → preflight → move up → convert delegations →
   summary). Rewrite `tests/test_agents.py` accordingly.
2. **Session unification.** Delegate persistence → `sessions/session_N/`
   (allocate at start, finalize in `finally`); kind/strategy-aware
   `sessions_index` (perf rollups filter `kind == tick_loop`); consult
   persistence + retention cap; learnings strategy-prefix provenance in
   `append_learning`.
3. **MCP surface.** `strategy_id` → `agent_slug` on `manage_routines`/
   `manage_skill`; `start_agent(agent_slug, strategy=…)`; agent-level
   `risk_limits` frontmatter on create/update; rewrite tool docstrings;
   fix `agents/routine_builder/AGENT.md` prose (6 refs) and
   `agent_builder` SKILL.md (5 refs); grep-audit all agent bodies/skills.
4. **Web + frontend.** Sessions/experiments/learnings tabs move to
   `AgentDetail` with a strategy filter chip; `StrategyDetail` slims to a
   playbook editor; flatten session/lifecycle routes to `/agents/{slug}/…`;
   `api.ts` types.
5. **Docs.** `agent-framework.md`, strategy-engine doc cross-references.

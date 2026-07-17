# Insights, Memories, and learnings.md: How They Relate Today, and How to Simplify

Goal (owner-stated): **store insights for each run, and propagate the most
durable ones to `learnings.md` at agent level** — with as few overlapping data
structures as possible.

This doc inventories every structure that currently touches that goal (§1–3),
shows what the two `memecoin_trender` runs actually wrote where (§2), lays
out simplification options (§4), examines how the prior Condor (commit
`13d0414`) handled the same problem (§5) and how three other agent frameworks
do — OpenClaw, karpathy/autoresearch, and Nous Research's Hermes (§6) — and
lands on a final recommendation (§7) with migration steps (§8). §10 records
two structural clarifications that came out of review: why `executors.jsonl`
is agent-scoped, and the deduplicated run record (prompt.md + journal.md +
slim tick events). Companion doc: `docs/agent-history-comparison.md` (§4.3,
§5, §6 cover the history of how we got here).

Context for "best of breed": Condor's stated purpose is to *create and run
autonomous trading agents that learn and improve* — so the learnings
pipeline is not a convenience feature, it is the product's core loop.

---

## 1. What exists today (who writes it, who reads it)

Six structures currently hold some flavor of "something the agent knows":

| # | Structure | Written by | Read by | Cap | Role (intended) |
|---|---|---|---|---|---|
| 1 | `runs/{ulid}.jsonl` | engine, automatic (every tick: prompt, response, `decision`, `state`, metrics, tool calls) | projection → next tick's `[CURRENT STATUS]` / `[RECENT DECISIONS]` (last 3) | none | per-run operational history + working memory |
| 2 | `learnings.md` | agent, explicit `record_learning(text)` | every tick's prompt, whole file → `[LEARNINGS]` | 40 bullets, oldest evicted | durable cross-run operational knowledge |
| 3 | `store/memory/memories/*.md` | agent/chat, explicit `manage_memory(write)` | via #4 | none | facts about the USER |
| 4 | `store/memory/MEMORY.md` | derived (reindex on every memory write) | every tick's prompt → `[USER MEMORY]` | — | injectable index of #3 |
| 5 | `store/memory/audit.log` | derived (append on every memory write/delete) | humans, `manage_memory(action="audit")` | 500 entries | provenance of #3 |
| 6 | repo-root `store/memory/` | chat (global tier) | chat prompt | none | user facts shared across agents |

\#4 and #5 are *derived* from #3 — they are not independent stores, and they
cost nothing to keep once #3 exists. The real overlap question is between
**#1, #2, and #3**: three independently-written places an agent can put an
insight, plus a fourth tier (#6) for user facts.

### Current flow

```
                              ┌──────────────────────────────────────────┐
                              │                ONE TICK                  │
                              └──────────────────────────────────────────┘
   [prompt sections]                          │
   [LEARNINGS] ◀──── learnings.md (≤40) ──┐   │
   [USER MEMORY] ◀── MEMORY.md ◀─reindex─ │ ┐ │
   [RECENT DECISIONS]+[CURRENT STATUS]    │ │ │
        ▲                                 │ │ ▼
        │ projection (fold, last 3)       │ │ agent decides, acts, observes
        │                                 │ │ │
   runs/{ulid}.jsonl ◀── engine emits ────┼─┼─┤  AUTOMATIC: prompt, response,
   (decision, state, metrics,             │ │ │  decision line, metrics,
    tool calls, prompt artifact)          │ │ │  tool calls — every tick
                                          │ │ │
        learnings.md ◀── record_learning ─┘ │ │  EXPLICIT: "durable operational
        (flat bullets, run-tagged)          │ │  fact" — agent's judgment call
                                            │ │
        store/memory/memories/*.md ◀────────┘ │  EXPLICIT: "stable fact about
        (+ MEMORY.md, audit.log derived)   ◀──┘  the USER" — agent's judgment
                                                 call (frequently misjudged)
```

Three explicit-or-automatic write channels, four prompt sections reading them
back. Every structure is individually defensible; together they give the agent
three places to put the same thought.

---

## 2. What the two runs actually propagated

Ground truth from `memecoin_trender` runs 1 (`01KXNP3DFJ…`, 92 ticks) and 2
(`01KXNZZGWN…`, 32 ticks):

| Channel | Run 1 | Run 2 | Content quality |
|---|---|---|---|
| run stream decisions/state | not emitted (pre-fix gap) | 31 ticks, automatic | correct working memory ("2/3 open, 1 slot — scanning") |
| `record_learning` → learnings.md | tool didn't exist | 4 calls | genuinely durable: "4h SL cooldown is global to the runtime, not per-run"; "pumpCmXqMfrs doesn't reliably hit 3% TP in 600s" |
| `manage_memory` → store/memory/ | **16 writes** | 2 writes | almost all per-trade ephemera: `cfp_kpq1_closure_tick10`, `hbull_stoploss_tick19`, `ansem_takeprofit_tick92`, … 16 of the 18 memories are trade closures or tick-scoped notes; ~2 are arguably durable (`runtime_cooldown_blacklist`, `standdown_tick50`) |

### The overlap, concretely: one trade, four copies

ANSEM's take-profit on run 2 tick 30 landed in **four** places:

1. `store/notifications.jsonl` — `🟢 Exited 9cRCn9rG (take_profit) — +0.000681071 SOL (+3.41%)` (engine, automatic)
2. `agents/memecoin_trender/executors.jsonl` — the executor's `closed/CLOSED` lifecycle events (engine, automatic)
3. `runs/01KXNZZGWN….jsonl` — tick 30's `decision` ("…ANSEM closed take_profit +0.0007 since last tick…") and `metrics` (engine, automatic)
4. `store/memory/memories/ansem_takeprofit_tick30_run2.md` — the agent *hand-copied the same fact* into a memory file (explicit, one tool call spent)

Copies 1–3 are free, automatic, and correctly scoped. Copy 4 cost a tool call,
polluted the user-facts store, duplicated an earlier memory
(`ansem_takeprofit_tick92`, same token, same conclusion), and now inflates
every future prompt via the injected index. The near-duplicate warning added
on 2026-07-16 flags this at write time, but the structural invitation remains.

### Why the agent does this

Two reasons, both fixable:

- **History taught it.** During the gap window, the prompt explicitly routed
  operational learnings into `manage_memory`; run 1 built 16 examples, and the
  agent sees those examples in `[USER MEMORY]` every tick and imitates them.
- **It wants a per-run scratchpad**, and `manage_memory` is the only explicit
  "save this" tool whose results it can see in the next tick's prompt — it
  didn't trust (or know) that the run stream already records everything and
  feeds the last 3 decisions back.

---

## 3. The key realization: the per-run insight store already exists

The owner goal is "store insights for each run, propagate the most durable to
learnings.md". Reading the runs, the first half is **already fully served by
structure #1**:

- Every tick's decision line (the agent's own one-sentence insight) is
  recorded automatically and fed back via `[RECENT DECISIONS]`.
- Every trade outcome, metric series, tool call, and prompt is on the stream.
- The markdown artifacts (`{run-start}Z.artifacts/`) make it human-readable.

Nothing per-run needs a second home. The only *judgment call* in the whole
pipeline is "which of this run's observations are durable?" — and that is
exactly what `record_learning` expresses, at the moment the insight occurs.
The propagation the goal asks for is a **decision, not a data structure**.

---

## 4. Simplification options

### Option A — status quo + prompt discipline (do nothing more)

Keep all six structures; rely on the corrected prompt guidance and the
near-duplicate warning. **Rejected as the end state**: run 2 already showed
habit residue (2 more ephemera memories written *after* the guidance was
fixed), and the 16 bad examples remain in the index teaching the pattern.

### Option B — two structures per agent (recommended — extended by §7)

Per agent, exactly two places hold knowledge:

1. `runs/{ulid}.jsonl` — per-run insights, automatic, complete.
2. `learnings.md` — durable cross-run knowledge, explicit `record_learning`.

The agent-tier memory store (#3/#4/#5) **leaves the trading-agent surface**:

- Remove `manage_memory` from the agent tick's tool preload; drop the
  "MEMORY (about the user)" prompt section and its write guidance.
- `[USER MEMORY]` injection switches to the **global tier** (repo-root
  `store/memory/`, #6) — read-only context about the owner, maintained by the
  chat brain, shared by all agents. A trading agent almost never *discovers*
  a user fact mid-tick; when one surfaces, it reaches the global store via
  the chat (where the user actually states preferences).
- `agents/{slug}/store/memory/` is deleted (after pruning — §5). The
  `MemoryStore` code is untouched; only the per-agent tier stops being used
  by agent runs. Chat keeps its global store, index, and audit exactly as-is.

Net: **one write channel per kind of knowledge** — automatic for per-run,
explicit for durable, none for user facts (agents consume, chat curates).

```
TARGET (option B)
                              ┌──────────────────────────────────────────┐
                              │                ONE TICK                  │
                              └──────────────────────────────────────────┘
   [LEARNINGS] ◀────────── learnings.md (≤40, agent-level)   │
   [USER MEMORY] ◀──────── repo-root store/memory (global,   │
        ▲                  chat-curated, agents read-only)   ▼
        │                                          agent decides, acts
   [RECENT DECISIONS]+[CURRENT STATUS]                        │
        ▲                                                     │
        │ projection                                          │
        │                                                     ▼
   runs/{ulid}.jsonl ◀────── engine, AUTOMATIC ── every tick: decision,
        │                                         state, metrics, tools,
        │                                         prompt artifact
        │
        └─ "this observation is durable" ──▶ record_learning(text)
                    (the ONE explicit judgment)        │
                                                       ▼
                                                 learnings.md
```

**Tradeoffs**: an agent can no longer persist a *user* fact itself (accepted:
that path produced 16 ephemera and ~0 genuine user facts across two runs);
and per-run "notes to self" live only in the decision line + response
(accepted: that's what they were on the stream anyway, and `[RECENT
DECISIONS]` feeds the last 3 back).

### Option C — explicit per-run insight events + end-of-run promotion pass

The literal reading of the goal: an `insight` event type (or
`record_insight(text)` tool) writes run-scoped insights to the stream; a final
distillation step (last tick of the run, or a shutdown hook) reviews them and
promotes the durable few via `record_learning`.

**Rejected for now**: this re-adds exactly the machinery this codebase has
deliberately deleted **twice** (the curation loop in the earlier refactor
series; categories/promotion in Phase 4). It also adds a failure mode — runs
that end by crash/stop skip the promotion pass, silently losing insights —
which then wants *its own* recovery sweep. Run 2 is evidence that write-time
promotion works: the agent produced 4 durable learnings mid-run with no
batch pass. Revisit only if write-time judgment demonstrably misses things.

### Option D — one store: fold learnings into the memory store

Make `learnings.md` a memory `type="learning"`; one store, one index, one
tool. **Rejected**: it inverts the simplification — the highest-traffic
channel (learnings) inherits the heaviest structure (per-fact files + index +
audit + reindex-on-write) and loses the rolling 40-cap file that makes
learnings self-limiting and trivially human-editable. The single file IS the
minimalist design.

---

## 5. How the prior Condor (13d0414) did it — and what its lifecycle got right

Full detail in `docs/agent-history-comparison.md` §4–5; the shape relevant
here:

- **Per-session insights** lived in `journal.md`'s `## Decisions` section
  (agent tool call, capped at 20 lines) plus engine-written Summary/Ticks/
  Snapshots sections — markdown that the engine regex-parsed back every tick.
- **Durable insights** went to a categorized `learnings.md` via
  `journal_write(entry_type="learning", category="market|execution")`, with
  three lifecycle features the current system dropped:
  1. **Write-time fuzzy dedupe** — new entries with >50% word overlap against
     ANY existing entry were silently dropped; the prompt told the agent
     "duplicates are auto-filtered", so it could write freely.
  2. **Caps with silent eviction** — 20 entries per category, oldest dropped.
  3. **Promotion with provenance** — `promote_learning` moved an entry to
     `## Promoted` once it had been folded into a skill, keeping a record of
     what knowledge fed which capability.
- There was **no automatic distillation** — an earlier "curation loop" had
  already been deleted before 13d0414, and Phase 4 then deleted the
  categories/promotion/dedupe machinery too ("curation is the agent's job
  now").

Verdict with hindsight: the prior version's *storage* was the problem
(markdown as a concurrently-mutated database feeding the risk engine), but
its *learnings lifecycle* was ahead of what we have now — dedupe stopped
flooding, and promotion gave learnings somewhere to graduate to. The current
system fixed the storage and lost the lifecycle. §7 restores the lifecycle
without restoring the machinery.

---

## 6. How other frameworks manage memory and self-improvement

Three contemporary designs, spanning the spectrum from journal-style to
artifact-style learning. (Sources: docs.openclaw.ai/concepts/memory,
github.com/karpathy/autoresearch, hermes-agent.nousresearch.com memory docs.)

### 6.1 OpenClaw — layered markdown + opt-in consolidation

- **Structures**: plain markdown in the workspace. `MEMORY.md` is "the
  curated long-term layer — durable facts, preferences, and decisions.
  Loaded at the start of a session." `memory/YYYY-MM-DD.md` daily notes hold
  running context; today's and yesterday's auto-load on session start.
  Optional `DREAMS.md` for consolidation summaries.
- **Write path**: the agent writes files itself (explicit); before context
  compaction "OpenClaw runs a silent turn that reminds the agent to save
  important context to memory files."
- **Promotion**: the agent "periodically distills material from daily notes
  into MEMORY.md and removes stale entries". An opt-in **"dreaming"** pass
  automates this: it "collects short-term recall signals, scores candidates,
  and promotes only qualified items into long-term memory" with
  score/recall-frequency/query-diversity thresholds.
- **Bounds**: if MEMORY.md exceeds the bootstrap budget the injected copy is
  truncated (file kept intact). Retrieval beyond injection: `memory_search`
  (hybrid vector+keyword) and `memory_get`.
- **Read-across to Condor**: OpenClaw's daily notes ≈ our run stream (the
  short-term layer that costs nothing); its MEMORY.md ≈ our learnings.md.
  Notably, even OpenClaw ships its automated consolidation ("dreaming") as
  **opt-in** — the default is exactly our model: the agent promotes at the
  moment of insight. Its pre-compaction save-turn solves a problem we don't
  have (our ticks always start fresh and re-read the files).

### 6.2 karpathy/autoresearch — the artifact IS the memory

- **Structures**: "three files that matter". `program.md` — human-maintained
  instructions; `train.py` — "the single file the agent edits"; logs.
- **Learning loop**: no memory store at all. The agent edits the working
  artifact, "checks if the result improved, keeps or discards, and repeats."
  Knowledge persists **as the improved artifact itself**, gated by an
  objective metric (loss). Survival-of-the-fittest, fully readable in diff.
- **Read-across to Condor**: trading has the same property ML research has —
  **an objective outcome signal**. The autoresearch analog maps cleanly:
  `train.py` ≈ `AGENT.md` (the one spec, already hashed and versioned per
  save), the eval run ≈ our `kind=experiment` dry run, and "keep or discard"
  ≈ updating the spec only when an experiment validates the change. This is
  the endgame of self-improvement that a notes file can never reach: a
  learning that just sits in `[LEARNINGS]` depends on the model re-reading
  and re-believing it every tick; a learning folded into AGENT.md *is* the
  agent from then on.

### 6.3 Hermes (Nous Research) — small, hard-capped, self-editing memory

- **Structures**: two files, deliberately tiny — `MEMORY.md` (~800 tokens,
  environment facts and lessons) and `USER.md` (~500 tokens, user profile) —
  plus SQLite full-text search over past session transcripts for long-tail
  recall.
- **Injection**: rendered into the system prompt "once at session start and
  never changes mid-session" (frozen snapshot, preserves prefix cache).
- **Write path**: proactive agent writes via three verbs — **add / replace /
  remove** (substring-matched). The key mechanism: memory has **hard
  character limits, and "the memory tool returns an error instead of
  silently dropping entries"** — when full, the agent must consolidate or
  remove before it can add. Curation is forced by the write API, not by a
  background process.
- **Self-improvement**: a post-turn review can save corrections and workflow
  lessons as memory entries or procedural skills, optionally staged behind
  `write_approval: true` for human review.
- **Read-across to Condor**: Hermes is the strongest critique of our current
  learnings lifecycle. Our 40-bullet cap **silently evicts the oldest
  learning** — which for a trading agent means durable negative knowledge
  ("token X reverses hard") ages out precisely because it's old, while
  fresher chatter survives. Hermes' error-on-full + replace/remove verbs
  turn the cap from a silent data-loss mechanism into a forcing function for
  consolidation — with zero background machinery. Its session-FTS also maps
  to something we already have for free: greppable `runs/*.jsonl` +
  markdown artifacts.

### 6.4 Tradeoff table

| | Prior Condor (13d0414) | Condor today | OpenClaw | autoresearch | Hermes |
|---|---|---|---|---|---|
| Short-term / per-run | journal.md sections (parsed back!) | run stream (automatic, projected) | daily notes (auto-loaded) | logs | session transcripts (SQLite FTS) |
| Long-term store | learnings.md, categorized | learnings.md, flat | MEMORY.md | the artifact (`train.py`) | MEMORY.md + USER.md |
| Cap behavior | 20/category, **silent drop** | 40, **silent drop** | inject-side truncation | n/a | hard limit, **tool errors when full** |
| Dedupe/curation | write-time fuzzy dedupe | advisory warning only | agent distills; opt-in "dreaming" scores+promotes | keep-or-discard by metric | forced consolidation via replace/remove |
| Promotion target | `## Promoted` (→ skills) | none | daily → MEMORY.md | eval-gated artifact edit | memory → procedural skills |
| Objective gating | none | none | recall-frequency scores | **loss metric** | human `write_approval` (optional) |
| Machinery cost | med (parsers, categories) | minimal | med-high (indexing, dreaming) | ~zero | low (limits + 3 verbs) |

The convergent findings across all five: (1) **two layers is enough** — a
cheap automatic short-term record and one small curated long-term store;
(2) **the long-term store must be small and bounded**, and what happens at
the bound is the whole design — silent eviction is the worst answer on the
table; (3) **promotion should be explicit and cheap**, and the best systems
give promoted knowledge somewhere *executable* to land (skills, the
artifact/spec) rather than letting notes pile up.

---

## 7. Final recommendation: option B + a bounded, self-editing learnings lifecycle

Keep option B's structure (two stores per agent: run stream + learnings.md;
agent-tier memory store removed, `[USER MEMORY]` read-only from the global
tier). On top of it, adopt the two best-of-breed mechanisms the survey
surfaced — both are lifecycle changes to the *existing* file, not new
structures:

**(a) Hermes-style bound: error-on-full + consolidation verbs.**
`record_learning` grows one optional argument and one behavior change:

- `record_learning(text, replaces="substring")` — replaces the matching
  entry (consolidating two observations into one stronger one, or updating
  a stale fact) instead of appending. A non-matching `replaces` is a loud
  error.
- At the 40-entry cap, a plain append **errors** ("learnings full — replace
  or consolidate") instead of silently evicting the oldest. The agent
  reading `[LEARNINGS]` every tick has everything it needs to pick what to
  merge.

This converts our silent data-loss point into the system's curation moment,
with no background process and no new files. (The old system's write-time
dedupe becomes unnecessary: the near-duplicate `warning` plus the cap's
forcing function cover it.)

**(b) autoresearch-style promotion ladder with experiment gating.**
Name the ladder explicitly in the agent's and chat's guidance — every layer
already exists:

```
runs/{ulid}.jsonl        AUTOMATIC   everything, every tick (free)
      │  "durable?" — record_learning at insight time
      ▼
learnings.md             EXPLICIT    ≤40, consolidated under pressure (a)
      │  "proven across runs?" — owner/chat folds into the spec,
      │   validated by a kind=experiment dry run before going live
      ▼
AGENT.md                 THE SPEC    versioned + hashed on every save;
                                     the learning becomes the agent
```

The top rung is what makes this a *learning* system rather than a
note-taking system: a learning that recurs and survives consolidation
("h1>70% momentum reliably TPs within 600s"; "never re-enter within 4h of a
stop-loss") graduates into AGENT.md's strategy body — proposed via chat,
checked by an experiment run, applied with `update_agent` (which already
hashes and freezes every version, so spec evolution is fully auditable).
No new machinery: the gate is the existing experiment kind, the write is the
existing update path, and the judgment stays with the owner (mirroring
Hermes' `write_approval` posture — apt for software that trades money).

What we deliberately do NOT adopt: OpenClaw-style automated consolidation
("dreaming") — Condor has deleted equivalent machinery twice, and even
OpenClaw ships it off-by-default; vector search — `runs/` is greppable and
learnings.md fits in every prompt whole; and any third storage layer.

---

## 8. Recommended migration — DONE 2026-07-16

1. ✅ **Pruned** `agents/memecoin_trender/store/memory/` (all 18 memories +
   the dir; the durable content already lived in learnings.md / the stream).
2. ✅ **Prompt**: `manage_memory` removed from the agent tick preload; the
   MEMORY section now says user memory is read-only advisory context and
   everything durable the agent discovers goes to `record_learning`.
3. ✅ **Engine + consult/delegate context**: `[USER MEMORY]` injects from
   `MemoryStore(None)` (global tier) everywhere agents run.
4. ✅ **Enforced, not just advised**: `manage_memory` itself now always
   resolves the global tier and REJECTS write/delete from agent-scoped
   sessions ("record durable operational knowledge with record_learning
   instead") — preload removal alone wouldn't stop a ToolSearch.
5. ✅ **Learnings lifecycle (§7a)**: `record_learning(text, replaces=…)`
   consolidates into exactly one matching entry (zero/ambiguous matches are
   loud errors); at the 40-cap a plain append ERRORS instead of silently
   evicting; the RECORDING prompt teaches consolidation.
6. ✅ **Ladder guidance (§7b)**: the promotion ladder is documented in the
   agent-builder skill ("How agents learn") and CONDOR.md's Memory section.
7. Still optional/later: drop the agent-tier branch of
   `condor/memory/paths.store_root` (now dead for agent runs).

Also landed in the same pass: per-agent custom hard rules got their general
home — **spec-declared entry guards** (`default_config.entry_guards` →
`condor/executors/guards.py`). The post-stop-loss cooldown moved out of core
(`condor/agents/token_blacklist.py`, deleted) into memecoin_trender's spec
(`stop_loss_cooldown: {hours: 4}`), enforced by a vetted core primitive on
the one executor-create path. The rule is hashed with the spec, applies only
to agents that declare it, and unknown guard names block loudly. This — not
routines (read-only by role) and not prompt text (proven insufficient) — is
the model for future agent-specific logic.

What deliberately did NOT change: the run-stream recording, the global chat
memory, exports, the experiment kind, `update_agent` spec hashing.

---

## 9. Summary

| Question | Answer today | Answer under §7 |
|---|---|---|
| Where do per-run insights live? | run stream (automatic) + memory ephemera (misuse) | run stream only |
| How do durable ones reach learnings.md? | `record_learning` at insight time | same, plus `replaces=` consolidation |
| What happens when learnings.md is full? | oldest silently evicted | tool errors; agent consolidates (Hermes pattern) |
| Where do proven learnings graduate to? | nowhere | AGENT.md via chat + experiment gate (autoresearch pattern) |
| Who owns user facts? | agent-tier store AND global tier | global tier only (chat-curated) |
| Memory-store tiers in the system | 2 (root `store/memory/` + `agents/*/store/memory/`) | **1 — root only**; agents read its index, never write. Per-agent memory has no remaining job: user facts are user-global (root tier), operational knowledge is `learnings.md`, configuration is `AGENT.md` |
| Explicit write channels per agent | 2 (`record_learning`, `manage_memory`) | 1 (`record_learning`) |
| Knowledge structures per agent | 5 (stream, learnings, memories/, MEMORY.md, audit.log) | 2 (stream, learnings) |

---

## 10. Two structural clarifications (implemented 2026-07-16)

### 10.1 Why `executors.jsonl` sits at agent level, outside `runs/`

It looks per-run (every line carries an `agent_id` — a run ULID), but that's
*attribution*, not scope. Three constraints pin it to the agent level:

1. **Executors outlive runs.** Stopping a run detaches its executors by
   default (`keep_position_on_stop`); they keep managing positions and keep
   writing lifecycle events after `run_ended`. Observed in run 2: the run
   ended 11:15:59, the last executor `closed` events landed 11:16:06–:09.
   A per-run `events.jsonl` would need writes into a *closed* run — exactly
   what the run stream's single-writer, closed-at-end design forbids. (Chat/
   CLI executors have no run at all; they log under a `_manual` slug.)
2. **It's the recovery record.** Per its docstring it is "the durable half of
   the executor state model": on restart the runtime folds it
   (`load_non_terminal`) to rebuild the open executor set and reconcile
   against the chain — without scanning every run of every agent.
3. **Its readers are cross-run.** The 4h stop-loss cooldown
   (`token_blacklist.py`) reads stop-outs across runs — the mechanism that
   blocked run 1's losers during run 2. Venue-truth and performance rollups
   fold it the same way.

Nor is it duplicative of the run stream — the two record different truths:

| | run stream | `executors.jsonl` |
|---|---|---|
| Records | what the **agent** saw/requested, tick-aligned | what **executors** did, machine-speed |
| Create | the `manage_executors` tool call (intent) | the `opened` event (outcome, full config+state) |
| Exits/fills | invisible until next tick's core data | authoritative `closed/failed` with reason+timing |
| Lifespan | ends at `run_ended` | continues while any position lives |

Intent (stream) / outcome (executor log) / summary (tick metrics, derived
from the latter). Removing any one loses information the others can't
reconstruct.

### 10.2 The run record, deduplicated (prompt.md + journal.md + slim events)

Measurement drove this: consecutive tick prompts in run 2 were **99%
identical** (~156 chars of real change per tick), yet each tick spilled a
~20KB prompt artifact — 640KB per 40-minute run, almost all repetition. The
fix splits the prompt record along **provenance**, not intuition:

| Prompt content | Recorded | Why |
|---|---|---|
| Frozen prefix (base prompt, AGENT.md instructions, config, session context, routines) | `prompt.md`, once per run | the frozen spec guarantees it cannot change mid-run |
| `[CURRENT STATUS]` / `[RECENT DECISIONS]` | not in the prompt record | derivable: a deterministic fold over the stream's own `state_snapshot` events (`journal.md` renders them for humans) |
| learnings / user-memory index / skills index | `context_changed` events — baseline at tick 1, then only on content change | external mutable files with out-of-band writers (operator PUT, chat) and no other per-tick history; a hash check each tick catches any writer |
| tick info, core data, risk state | inline on `tick_started` (`prompt_suffix`) | genuinely per-tick, not derivable elsewhere |
| user directives | already `directive` events | durable on the stream since Phase 4 |

Each `tick_started` also carries `prompt_sha256` — the hash of the full
assembled prompt — so "the exact prompt at tick N" is *verifiable*:
`prompt.md + last context_changed ≤ N + tick N's suffix + acked directives`,
checked against the hash rather than trusted.

The run folder becomes two readable files plus the stream (per-tick spill
files disappear in the common case — the slim suffix stays inline):

```
runs/
├── {ulid}.jsonl                      # events; ~2-3KB prompt suffix inline/tick
└── {YYYY-MM-DD_HH-MM-SS}Z.artifacts/
    ├── prompt.md                     # frozen prefix, once (~13KB)
    └── journal.md                    # one line per tick: time, tick #,
                                      #   pnl/open/exposure, decision
```

`journal.md` deliberately reuses the old name with none of the old role: it
is a **generated view** appended by the engine — the projection still folds
the JSONL, and nothing ever parses these files back (the same one-way rule
as `exports.py`).

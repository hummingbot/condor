# Agent Operational History: Old (markdown-file) vs Current (RunStore) Model

**Old** = commit `13d0414` ("simpler agent framework: executor matrix, platform hardening, and the simplification plan") — the last commit before the PR #158 changes, i.e. the refactor-07-era system whose docstrings still describe `sessions/`, `journal.md`, and snapshot files.
**Current** = branch `spike/simpler-agent-framework` (HEAD `66d9bec` + the 2026-07-16 gap fixes described in §7/§5.2).

Behavioral witnesses: two live `memecoin_trender` runs —
**run 1** `01KXNP3DFJSDYSDZCGW2TZF21V` (07:43–09:51 local, pre-fix code) and
**run 2** `01KXNZZGWN2FM3X7N66VBAMWKH` (10:36–11:16 local, post-fix code).
§9 analyzes run 2 against run 1 and documents a stop-control incident.

---

## 1. The one-sentence difference

The old system used **markdown files as a database** — the engine and the agent
both wrote sections of `journal.md`, snapshot files, and `learnings.md`, and the
engine (including the risk engine) **regex-parsed that markdown back** to build
the next tick's context and its risk numbers. The current system records
**one append-only JSONL event stream per run** (`RunStore`); everything that used
to be parsed out of markdown is now a fold ("projection") over events, and
markdown exists only as a one-way generated export.

---

## 2. On-disk layout

### 2.1 Old (13d0414)

```
agents/{slug}/
├── AGENT.md                        # identity + domain knowledge
├── learnings.md                    # agent-level, CATEGORIZED, cross-session
│   ├── "## Market Observations"    #   ≤20 entries per category (silent drop)
│   ├── "## Execution Notes"
│   ├── "## Promoted"               #   learnings folded into skills (provenance)
│   └── "## Retired Insights"       #   template only — nothing wrote it
├── strategies/
│   └── scalp_v1.md                 # playbook: tactic + default_config
├── sessions/                       # THE stateful track record
│   └── session_1/                  # number allocated via mkdir(exist_ok=False)
│       ├── meta.yml                # strategy, status, model, started_at/ended_at
│       ├── config.yml              # frozen launch config
│       ├── journal.md              # 5 sections, mixed engine/agent writers
│       └── snapshots/
│           ├── snapshot_1.md       # FULL prompt + response + tool calls, tick 1
│           ├── snapshot_2.md
│           └── ...                 # capped at 100, oldest deleted
├── experiments/
│   └── 2026-07-11-e1.md            # one flat snapshot file per experiment
├── delegations/
│   └── 2026-07-11-d1.md            # transcript (written as "husk", rewritten at end)
├── store/user_{user_id}/           # memory keyed by (agent, user)
│   ├── MEMORY.md                   # one-line-per-memory index
│   ├── memories/<slug>.md
│   └── audit.log
├── skills/  routines/
```

Identity was a **parsed grammar**: session id `{slug}_N`, experiment `{slug}_eN`,
delegation task `{slug}-dN`, consult attribution `{slug}-cYYYYMMDDHHMMSS` — all
matched by regexes (`split_agent_id`, filename regexes in `journal.py`).

### 2.2 Current (spike/simpler-agent-framework)

```
agents/{slug}/
├── AGENT.md                        # THE one spec (strategy folded in, §5.3)
├── learnings.md                    # FLAT bullet list, ≤40, no categories
├── runs/                           # 0700 — the one operational history
│   ├── 01KXNP3DFJ….jsonl           # one append-only event stream per run (0600)
│   └── 2026-07-16_17-36-00Z.artifacts/  # >16KB payloads spill here as markdown
│       └── 17-36-00Z-tick_started.md    #   (dir = run start, files = event time,
│                                        #    both UTC; path+hash ref on the event)
├── executors.jsonl                 # append-only executor lifecycle log
├── store/memory/                   # agent-tier memory (no user_{id} — single-user)
│   ├── MEMORY.md
│   ├── memories/<slug>.md
│   └── audit.log
├── skills/  routines/
```

`sessions/`, `experiments/`, `delegations/`, `strategies/` are **gone** — deleted
without migration (§7.3, commit `1293fbe`, an explicit owner decision: "This
deletion is irreversible"). Sessions/experiments/delegations/consults/scheduled
runs are now all the same artifact — a run — distinguished only by a `kind`
field in the `run_started` event. The run id is an **opaque ULID**; "nothing
parses a run id" (`runstore.py` docstring). A human-facing `display_seq` is
stored in the event instead of encoded in the id.

---

## 3. Session snapshots

### 3.1 Old: two things called "snapshot"

**(a) Full snapshot files** — after every non-experiment tick, the engine wrote
`sessions/session_N/snapshots/snapshot_{tick}.md` containing:

```
# Snapshot #{tick} — {timestamp}
<details><summary>System Prompt (NNNN chars)</summary>  ← the ENTIRE prompt, every tick
## Executor State
## Risk State
## Agent Response
## Tool Calls (N)        ← full JSON input, output truncated to 2000 chars
## Stats                 ← duration
```

Capped at `MAX_SNAPSHOTS = 100`; oldest deleted after each write. Nothing read
these back programmatically — they were forensic/debugging artifacts.

**(b) Inline snapshot lines** — one metric line per tick appended to the
journal's `## Snapshots` section:
`- 2026-07-11 09:30 | pnl=$+1.20 | volume=$1,234 | open=2 | exposure=$400.00`.
These WERE read back: the risk engine's drawdown came from parsing this series.

### 3.2 Current: `state_snapshot` events in the run stream

Both roles collapse into events on the same JSONL stream. Per tick the engine
emits (engine.py `_tick` tail):

```json
{"type": "tick_started", "tick": 12,
 "payload": {"prompt": "<the exact prompt this tick ran with — typically
              >16KB, so spilled as markdown to
              {run-start}Z.artifacts/{HH-MM-SS}Z-tick_started.md
              with a sha256 + preview reference here>"}}
{"type": "state_snapshot", "tick": 12,
 "payload": {"response": "<agent response, first 2000 chars>",
             "decision": "<first non-empty response line, ≤240 chars>",
             "state": "Last tick: #12 | pnl=+0.0006 SOL | open=2 | exposure=0.04 SOL",
             "metrics": {"total_pnl": …, "total_volume": …,
                         "total_exposure": …, "open_count": …},
             "duration": 41.3}}
{"type": "tick_completed", "tick": 12,
 "payload": {"actions": 3, "metrics": {…}, "duration": 41.3, "timed_out": false}}
```

(`prompt`, `decision`, and `state` were added by the 2026-07-16 gap fixes —
the original Phase-4 code emitted only `response`/`metrics`/`duration`; see
§7 for the gap that closed. `record_decision` on the shutdown path also emits
`decision` snapshots.) Key deltas vs old:

| | Old full snapshot | Current `state_snapshot` |
|---|---|---|
| System prompt | persisted verbatim every tick (unredacted markdown) | persisted verbatim every tick on `tick_started`, redacted, spilled to `.artifacts/` (~19–20KB/tick in run 2) |
| Response | full text | first 2000 chars |
| Tool calls | in the snapshot file | separate `tool_call` events, redacted, >16KB spills to `.artifacts/` |
| Metrics | inline journal line (parsed back) | typed `metrics` dict on the event |
| Retention | last 100 files | unbounded events in one stream |
| Secrets | none — raw prompt/tool payloads on disk | every payload passes a redactor (sealed key names + secret-shaped value patterns) |
| Durability | plain writes | flush per event; fsync on `run_started`/`run_ended`/`permission`/mutating `tool_call`; torn final line truncated on reopen |

---

## 4. journal.md

### 4.1 Old: a five-section read-write markdown database

```
# Journal - {slug}_N
## Summary       ← engine, rewritten every tick (3-line status); agent could
                   overwrite via journal_write(entry_type="state")
## Decisions     ← AGENT tool call: journal_write(entry_type="action")
                   "- **#{tick}** (HH:MM) {action} -- {reasoning} [{risk}]"
                   capped at last 20 lines; engine also wrote tick_blocked/errors
## Ticks         ← engine: "- tick#N | ts | actions=n | {response[:200]}"
## Executors     ← engine: whole section REWRITTEN each tick from live data
## Snapshots     ← engine: one metric line per tick (pnl/volume/open/exposure)
```

Two processes (engine main process + MCP subprocess) mutated the same file
through independent `JournalManager` instances, coordinated by an `fcntl.flock`
sidecar lock and atomic tmp+rename writes. Reads were regex section extraction
(`^## {name}\n(.*?)(?=^## |\Z)`), and bullet lines were split on `" | "` / `"="`
back into dicts, with an `(mtime, size)` cache.

**The risk engine ran on parsed markdown.** `get_total_exposure`,
`get_open_executor_count`, `get_drawdown_pct` (peak-vs-last over the parsed
`## Snapshots` pnl series) all came from journal parsing, with a `_num()` helper
tolerant of `$0.02` / `1,234.5 SOL`. The old code's own comment: "a parse crash
here blocks the agent from managing its own open positions."

Next-tick context read-back: `read_summary()` → `[CURRENT STATUS]`,
`get_recent_decisions(3)` → `[RECENT DECISIONS]`, `read_learnings()` →
`[LEARNINGS]`.

### 4.2 Current: journal.md does not exist

Its three jobs were split:

1. **Recording** → automatic events on the run stream. The tick prompt now says:
   "Your response and tool calls are recorded on the run automatically … no
   tool call needed." The agent no longer has a journal tool at all.
2. **Next-tick context** → `run_projection(events)` (`projections.py`): a pure
   fold producing `state`, `decisions`/`recent_decisions`, `tick_count`,
   `metrics_series`, `directives_pending`, `errors`. Nothing writes markdown,
   nothing parses markdown.
3. **Human reading** → `exports.py::render_run_markdown(meta, events)` renders
   a journal-looking document on demand. One-way by design: "nothing ever
   parses markdown back."

Risk numbers moved off markdown entirely: `RunMetricsTracker` is fed each tick
from the in-process native executor store (venue/record truth); the PnL series
is in-memory per run — a restart deliberately starts a fresh drawdown baseline
(§4.2 "engines are memory-only").

### 4.3 Tick dataflow, side by side

```
OLD (13d0414)                                 CURRENT (spike/simpler-agent-framework)
─────────────────────────────                 ─────────────────────────────────────────
        ┌────────────┐                                ┌────────────┐
        │ TickEngine │                                │ TickEngine │
        └─────┬──────┘                                └─────┬──────┘
   providers  │                                  providers  │
   (executors)▼                                  (executors)▼
  ┌───────────────────────┐                    ┌──────────────────────────┐
  │ journal.md            │  regex             │ runs/{ulid}.jsonl        │  fold
  │  Summary/Decisions/…  │──parse──┐          │  (append-only events)    │──projection─┐
  │  ← flock, 2 processes │         │          │  ← single writer + lock  │             │
  └───────────▲───────────┘         │          └────────────▲─────────────┘             │
              │ rewrite sections    ▼                       │ emit events               ▼
              │              ┌────────────┐                 │                    ┌────────────┐
              │              │ RiskEngine │ ← markdown!     │   RiskEngine ◄─────│ executor   │
              │              └────────────┘                 │   (pre-flight)     │ store (mem)│
              │                     │                       │                    └────────────┘
              ▼                     ▼                       ▼
   build_tick_prompt( learnings, summary,        build_tick_prompt( learnings, projection
        recent_decisions, risk, memory idx )          state/decisions, risk, memory idx )
              │                                             │
              ▼                                             ▼
         run_agent (fresh context)                     run_agent (fresh context)
              │                                             │
   ┌──────────┴──────────────┐                   ┌──────────┴──────────────┐
   ▼                         ▼                   ▼                         ▼
 record_tick / snapshot   agent tool calls:    state_snapshot +         agent tool calls:
 line / write_summary /   journal_write        tick_completed events    manage_memory
 save_full_snapshot       (action/learning/    (automatic, redacted,    (durable facts →
 (full prompt to disk)    state)               fsync'd where financial) store/memory/)
```

---

## 5. Learnings

### 5.1 Old: agent-written per tick, categorized, fuzzy-deduped

Contrary to the "journal propagates to learnings" mental model, at `13d0414`
there was **no end-of-session distillation and no curation loop** (grep for
distill/curation at that commit returns nothing — the curation loop had already
been removed in the earlier refactor series). The pipeline was:

```
per tick:  agent calls journal_write(entry_type="learning",
                                     category="market|execution", text="…")
              │
              ▼
   _append_learning_locked (flock)
              │  1. normalize (lowercase, strip punctuation/timestamps)
              │  2. DROP if exact normalized match anywhere, or
              │     word-overlap > 0.5 vs ANY existing entry
              │     (overlap = |words∩| / min(|a|,|b|))
              ▼
   learnings.md  "## Market Observations" / "## Execution Notes"
              │     entry: "- [ts] [{strategy}] text"  (strategy = provenance)
              │     cap: 20 per category, oldest silently dropped
              ▼
   optional: journal_write(entry_type="promote_learning")
              → moves entry to "## Promoted" (record of what fed a skill)
```

Read-back: every tick, all category sections re-rendered into the prompt as
`[LEARNINGS — do NOT repeat these, only add genuinely new insights]`. The
prompt told the agent duplicates were auto-filtered, so it could write freely.

### 5.2 Current: learnings.md survives, written via `record_learning`

`learnings.py` docstring: "The RunStore replaces journals; learnings survive as
explicit agent memory … The Phase-4 simplification drops the old category /
promotion / fuzzy-dedupe machinery — curation is the agent's job now."

- **Format**: flat `# Learnings` + timestamped bullets, `MAX_LEARNINGS = 40`
  total (was 20 × per-category), no categories, no promotion, no dedupe.
- **Read**: still injected into every tick's prompt (engine.py:437 →
  `[LEARNINGS — do NOT repeat these …]`).
- **Write**: the agent-scoped MCP tool `record_learning(text)` (added
  2026-07-16) → `append_learning()` under the flock. It resolves the agent
  from `CONDOR_AGENT_SLUG` and errors loudly in chat context. The operator
  endpoint `PUT /agents/{slug}/learnings` (wholesale replace) remains for
  curation. `manage_memory` guidance reverted to user-facts-only.

```
OLD:  tick insight ──journal_write(learning)──▶ learnings.md ──▶ next tick prompt
                     (auto-deduped, categorized)
CURRENT:  tick insight ──record_learning(text)──▶ learnings.md ──▶ next tick prompt
                     (flat, no dedupe — curation is the agent's job)
          user facts ──manage_memory(write)──▶ store/memory/ ──index──▶ prompt
```

Run 2 exercised this end-to-end: 4 `record_learning` calls (ticks 5, 11, 17,
28) landed durable pattern observations in learnings.md — e.g. "4h SL cooldown
persists CROSS-SESSION/RUN … the cooldown blacklist is global to the runtime,
not per-run".

⚠ **Historical gap (fixed 2026-07-16)**: as shipped, Phase 4 left
`append_learning` with zero production callers and the prompt routed
operational facts into `manage_memory` — learnings.md was a read-only fossil
that still shaped every prompt (run 1 demonstrated this: learnings.md never
grew while `store/memory/` accumulated per-trade notes). The `record_learning`
tool closed the gap, restoring §7.1's "appended via an explicit tool call".

### 5.3 Consequences of dropping dedupe

The old >50%-word-overlap filter meant a chatty agent couldn't flood learnings.
Now neither channel dedupes: learnings.md relies on the prompt's "do not
re-record what is already there", and `manage_memory` is one-file-per-fact
keyed by name — an agent that picks a new name per tick can accumulate
unlimited near-duplicates, and nothing compacts them. The index is injected
every tick, so memory growth directly inflates every future prompt. "Curation
is the agent's job now" is a real behavioral bet, not just a code deletion —
and run 2 shows the bet is already being tested: the memory store now holds
both `ansem_takeprofit_tick92` (run 1) and `ansem_takeprofit_tick30_run2`
(run 2) — the same token's take-profits as separate per-trade ephemera that
belong, if anywhere, in one learning.

---

## 6. Memories

Largely continuous — `MemoryStore` predates the refactor — with two changes:

| | Old | Current |
|---|---|---|
| Store path | `agents/{slug}/store/user_{user_id}/` — keyed by (agent, user) | `agents/{slug}/store/memory/` — no user key (auth deleted, single-user posture §5.5); global tier at repo-root `store/memory/` |
| Format | MEMORY.md index + `memories/<slug>.md` (frontmatter+body) + audit.log (cap 500) | identical |
| Agent access | `manage_memory` in tool preload; prompt: memory is ABOUT THE USER, "operational/market learnings go to the journal … NOT here" | same tool and same separation — after a detour: as shipped, Phase 4's prompt routed operational learnings INTO `manage_memory`; the 2026-07-16 fix restored user-facts-only and pointed learnings at `record_learning` |
| Prompt injection | index read fresh each tick → `[USER MEMORY — advisory]` | identical mechanism |

The detour left a visible residue: during run 1 the memory store absorbed
per-trade operational notes (`cfp_kpq1_closure_tick10`, `ansem_takeprofit_tick92`,
…), and in run 2 the agent still wrote two more trade-closure memories out of
habit-shaped context. The channels are separated again in the prompt, but the
memory store carries no cap and no dedupe, so what lands there stays.

---

## 7. Continuity between ticks (what the next tick actually knows)

Both systems intentionally run each tick with a **fresh context window**; all
continuity flows through the prompt. What each system injected:

| Prompt section | Old source | Current source | Status (verified in run 2's prompt artifacts) |
|---|---|---|---|
| `[CURRENT STATUS]` | `## Summary` (rewritten every tick by engine) | engine-emitted `state` on each tick's `state_snapshot`, folded by the projection | live — e.g. `Last tick: #6 \| pnl=-0.000239158 SOL \| open=3 \| exposure=0.06 SOL` |
| `[RECENT DECISIONS]` | last 3 `## Decisions` lines (agent-written via tool) | `decision` = first non-empty line of the tick response (the prompt asks the agent to open with a one-line decision), folded by the projection (last 3) | live — real decision lines appear in later ticks' prompts |
| `[LEARNINGS]` | learnings.md (agent-fed) | learnings.md (agent-fed via `record_learning`) | live |
| `[USER MEMORY]` | memory index | memory index (user facts) | live |
| Risk state | parsed journal markdown | in-memory tracker from executor store | live, sturdier |
| Core data | providers | providers | live |

⚠ **Historical gap (fixed 2026-07-16)**: as shipped, Phase 4's projection
expected `state`/`decision` payload keys that no normal-tick code emitted —
run 1's snapshots carried only `response`/`metrics`/`duration`, so
`[CURRENT STATUS]` and `[RECENT DECISIONS]` injected nothing and the agent's
inter-tick continuity rode entirely on executor state, (stale) learnings, and
memory. The fix emits both keys from the tick path; run 2's persisted prompts
show the loop closed. One quality wart remains: ACP text chunks sometimes
concatenate without newlines, so a "first line" can run together with a
following markdown header (e.g. `…scanning.**Step 3 — Judge:**`).

---

## 8. Lifecycle, identity, recovery

| Concern | Old | Current |
|---|---|---|
| Run identity | grammar-encoded: `{slug}_N`, `{slug}_eN`, `{slug}-dN`, `{slug}-cTS`; parsed by regex in many places | opaque ULID; `kind` ∈ {session, experiment, delegation, consult, scheduled} + `display_seq` inside `run_started` |
| Number allocation | `mkdir(exist_ok=False)` / `touch(exist_ok=False)` as locks | ULID collision-checked; `display_seq` = count of `*.jsonl` + 1 (advisory only) |
| Start record | meta.yml + config.yml written at start | `run_started` event: frozen spec + source/resolved spec hashes + AccountRefs + model (fsync'd) |
| Crash recovery | startup sweep flips `running` meta.yml → `interrupted` | `sweep_interrupted()` appends synthesized `run_ended{interrupted}` AND voids pending `permission` events (`interrupted_void`) so stale approvals can't fire |
| Experiments | separate flat file format + own regex parser | same stream, `kind: experiment` |
| Delegations | husk-then-rewrite transcript file + header-bullet parser | same stream, `kind: delegation` |
| Consults | persisted nothing | same stream, `kind: consult` |
| History UI | `sessions_index.py`: walk dirs, parse meta.yml + 3 different markdown formats with per-format regexes and mtime caches | `list_runs`: head event + tail event of each stream (cheap 64KB tail window) |

---

## 9. Run 2 validation (2026-07-16): the fixes in practice, plus a stop incident

Run 2 (`01KXNZZGWN2FM3X7N66VBAMWKH`, kind=session, display_seq 2) was launched
from OpenClaw at 10:35:51 local, immediately after restarting the server on the
gap-fix code, and stopped manually at 11:15:59.

### 9.1 Harness interaction — what the new format captured

| | Run 1 (pre-fix) | Run 2 (post-fix) |
|---|---|---|
| Duration / ticks | 2h08m / 92 ticks | 40m / 32 ticks (tick 32 cancelled mid-flight by the stop) |
| Event mix | run_started, tick_started(×N, empty), tool_call, state_snapshot(response/metrics only), tick_completed, run_ended | same + `prompt` on every tick_started (spilled to 32 artifacts, ~19–20KB each), `decision`+`state` on every snapshot |
| `[CURRENT STATUS]`/`[RECENT DECISIONS]` in prompts | empty (gap) | populated (verified in prompt artifacts) |
| record_learning | tool didn't exist | 4 calls → durable patterns in learnings.md |
| manage_memory | absorbed operational notes (mis-routed) | 2 calls — still trade-closure ephemera (habit residue; see §5.3) |
| Tool calls | ToolSearch + routines + executors | 31 ToolSearch, 26 manage_routines, 11 manage_executors, 4 record_learning, 2 manage_memory |

Observations from the stream:

- **Prompt artifacts grow slowly** (~18.7KB → ~20.9KB over 32 ticks) as status,
  decisions, and learnings accumulate — ~640KB of artifacts for a 40-minute
  run. Fine at this scale; a retention policy will eventually be wanted.
- **`tool_call` events recorded `input: {}` for MCP tools** (fixed since —
  see §9.4.1) — run 2's stream knows *that* `record_learning` ran on tick 5
  but not what it said; the arguments streamed in on ACP updates after the
  create event and were dropped, and the event was persisted at create time.
- **Tick 31 (11:14:21) is degenerate**: the entire agent response was "Model
  switched to claude-sonnet-4-6." — ~90 seconds before the OpenClaw restart
  (see 9.3). The new `decision` capture surfaced this immediately.
- The agent self-prefixed its learnings `[trend_position]` (mimicking the old
  entries it sees in the file) and once double-stamped a date — cosmetic, but
  shows the file's legacy content shapes new entries.

### 9.2 Trading performance, run 1 vs run 2

From the notifications stream (both runs, same conservative config: 0.02 SOL
positions, max 3 open, TP +3% / SL -5% / TTL 600s):

| | Run 1 | Run 2 |
|---|---|---|
| Entries / exits | 30 / 29 | 8 / 8 |
| Realized PnL | **-0.003239 SOL** | **+0.002152 SOL** |
| Exit mix | 8 TP / 16 time_limit / 4 SL / 1 early_stop | 2 TP / 4 time_limit / 0 SL / 2 early_stop |
| Entry rate | ~0.33 entries/tick | ~0.25 entries/tick |

Run 2 was materially more selective and avoided stop-losses entirely. Two
mechanisms plausibly contributed, both traceable in the artifacts:

1. **Cross-run negative knowledge finally reached the agent.** The runtime's
   4h post-SL cooldown blocked re-entry into run 1's losers (MENSA,
   TRUMPCOIN) — and the agent *recorded that as a learning* ("the cooldown
   blacklist is global to the runtime, not per-run"). Its other learnings
   ("pumpCmXqMfrs … doesn't reliably hit 3% TP within 600s", "JOTCHUA …
   marginal in both directions") were negative-selection facts available to
   every subsequent tick.
2. **Working memory made position state legible**: with `[CURRENT STATUS]` and
   `[RECENT DECISIONS]` populated, ticks consistently opened with correct
   slot accounting ("2/3 open, 1 slot available — scanning").

Caveats: 40 minutes vs 2 hours, different market hours, n=8 vs n=30 — this is
one favorable sample, not proof the fixes improve PnL.

### 9.3 The stop incident: a client-side wedge, not a control-path failure

Reported symptom: "stop the agent" didn't work; it worked only after exiting
and restarting OpenClaw and asking again. The logs on both sides pin the
failure to the OpenClaw client, and exonerate the Condor control path:

```
16:51Z          same OpenClaw session stops RUN 1 via control_run → success
17:35:51Z       run 2 started from OpenClaw (control socket working)
17:36–18:15Z    OpenClaw session log: NOTHING — no user message, no tool call.
                The first "stop the agent" never became a prompt: the client
                was wedged. Nothing reached Condor (no control-socket call,
                no server-side trace).
   meanwhile    the engine kept ticking every ~60–80s and opened THREE new
                positions (11:11:40, 11:12:52, 11:14:21 local) — correct
                per design (the engine lives in `condor.cli serve`, OpenClaw
                is just an MCP client), but disquieting while trying to stop.
18:14:21Z       tick 31 degenerate ("Model switched to claude-sonnet-4-6.")
18:15:53Z       OpenClaw trajectory: `session.started` — the RESTARTED client's
                first prompt is "stopt he agent"
18:15:58Z       control_run(run_id, stop) → {"stopped": true, 3 executors}
18:15:59Z       run_ended {status: stopped, reason: manual stop};
                exits land 11:15:57–11:16:09 (2 early_stop + 1 coincident TP)
```

Condor's `control_run` is a stateless one-request-per-connection unix-socket
call — it survived the 10:35 server restart without re-registration and went
2-for-2 when actually invoked. What the incident does expose:

- **No harness-independent stop.** When the chat client wedges, the only
  paths to the engine are another MCP client or the web route. A trivial CLI
  escape hatch (`condor stop <run_id>` hitting the control socket directly)
  would make the worst case "open a terminal" instead of "restart the client
  and hope".
- **The engine's liveness is invisible to a wedged operator.** Notifications
  kept flowing to the outbox, but the operator's one window was frozen. The
  run stream had the truth the whole time (`list_runs` → status: running).

### 9.4 Residual follow-ups after run 2

1. ~~Capture MCP tool-call inputs~~ — **fixed 2026-07-16 (second pass)**.
   Root cause: Claude Code streams MCP arguments on `tool_call_update` events
   *after* the create event; the ACP client dropped `rawInput` on updates and
   the engine persisted the event at create time. Now updates patch `input`,
   and the audit hook fires when a call reaches terminal status (still-open
   calls are flushed at stream end), so `tool_call` events carry
   name + input + output + final status.
2. ~~CLI stop escape hatch~~ — **shipped 2026-07-16 (third incident)**:
   `condor stop [run_id|slug] [--close]` hits the control socket directly.
   Used in anger the same hour: a third stop-hang reproduced, and this time
   the process tree pinned it — OpenClaw is a client/daemon split, its
   long-lived **gateway daemon** (not the TUI the user restarts) owns the
   session and the condor MCP/channel subprocesses; those dated from 07:56
   and had survived two Condor server restarts. The user's "stop" never
   became a session event (session log untouched during the spinner), while
   `condor stop`-style direct socket calls stopped the run instantly —
   Condor's control path is now 3-for-3. Fix on the OpenClaw side: restart
   the gateway (`openclaw gateway restart`), not just the TUI.
3. Normalize `decision` extraction against ACP chunk concatenation.
4. Decide an artifacts retention policy before long-lived runs.
5. ~~Memory ephemera accumulation~~ — **mitigated 2026-07-16 (second pass)**:
   `MemoryStore.write` now returns an advisory warning when a new memory's
   text >50%-overlaps an existing one (never blocks; overwriting a memory by
   its own name never warns). The existing per-trade memories from runs 1–2
   are still in the store and worth pruning by hand.
6. Tick-31 "Model switched" degenerate response — **cause narrowed, harness
   hardened 2026-07-16 (second pass)**. The text is a Claude Code session
   notice, not agent output: each tick spawns a fresh `claude-agent-acp`
   whose default model comes from the *user's* Claude Code `settings.model`;
   Condor then pins the requested model via `session/set_model`, and the
   bridge can emit "Model switched to <id>." as session text. Tick 31 spent
   12s, ran zero tools, and produced only the notice — consistent with a
   bridge-side switch (usage-limit fallback or default-model change; the
   server's stdout logs, which record the per-session model resolution, were
   not retained to settle it). Hardening: (a) the notice is filtered out of
   `decision` so it can't pollute `[RECENT DECISIONS]` (it stays in
   `response`), and (b) every `tick_completed` now records the resolved
   `model` id, so a switch is visible in the stream instead of only inside a
   response string. Remaining exposure: a *mid-turn* switch won't update the
   recorded id until the next tick.
7. ~~(from run 4 — CRITICAL) The `shutdown` verb's leftovers sweep violates
   the shutdown policy and corrupts terminal records~~ — **fixed 2026-07-16**
   (§9.5.3): the blanket `keep_position=False` sweep is gone — with live
   engines, `run_shutdown`'s slug-wide winddown is the whole job; with none,
   the new `winddown_slug` runs the same policy-resolved baseline + verify
   and notifies via the outbox. `stop_executor` also rebuilds a stale
   in-memory executor (loop done → connector closed) from the durable record
   with a fresh connector, so a *legitimate* detach-reactivation close no
   longer fails on a closed client. Executors whose loop exits FAILED now
   alert the operator via the outbox regardless of `notify_trades` or run
   state. The two corrupted run-4 records are restored by a one-off script
   (corrective append; see §9.5.3).
8. ~~(from run 4) `is_dangerous_tool_call` key mismatch~~ — **fixed
   2026-07-16** (§9.5.4): `tool_call_name`/`tool_call_input` helpers in
   gating.py accept all three producer shapes (`tool`/`title`+`rawInput`/
   `name`+`input`); missing arguments now classify as dangerous and
   `check_executor_action` fails closed on them; experiment mode cancels
   argument-less executor calls. The audit `mutating` flag and the ACP-layer
   risk gate (early UX check + per-tick accumulation) are live again.

### 9.5 Run 4 validation (2026-07-16 23:11Z): the record shape works end-to-end — and the shutdown sweep has a real bug

Run `01KXPK6N7TZZ7QVC31ZQCDNJKK` (session #4, `claude-acp:sonnet`): started
23:11:48Z, ended 23:23:39Z (11m51s), 8 completed ticks + tick 9 interrupted
by shutdown, 54 events. First run after the server restart, so the first
production exercise of the full refined record shape.

#### 9.5.1 Everything shipped this week worked in production

- **Companions + naming**: `runs/2026-07-16_23-11-48Z.artifacts/` with
  `prompt.md` (frozen prefix, once), `journal.md` (one line per completed
  tick, decision + state), and 9 timestamped `{HH-MM-SS}Z-tool_call.md`
  spill files (~14.5 KB each — the per-tick momentum-scan output).
- **Prompt forensics**: every `tick_started` carries `prompt_suffix`
  (~10 KB) + `prompt_sha256`; the suffix grew tick-over-tick as
  `[RECENT DECISIONS]` accumulated — exactly the dedup'd shape intended.
- **`context_changed` on-change detection**: baseline at seq 2; the ONLY
  other emission (seq 31) landed immediately after the agent's
  `record_learning` call grew `learnings.md` 8367 → 8618 chars. Hash
  detection fired precisely once, precisely when the input changed.
- **Tool-call inputs** (follow-up #1): full arguments captured on every MCP
  call — the three `manage_executors` creates are complete audit records
  (token, size, TP/SL, slippage).
- **Learning loop**: the agent recorded one durable learning (the tick-5
  stop-loss close, 32/40 used) and *applied* the 4h SL-cooldown at tick 8:
  "TRUMPCOIN (F4GpAFr6) rejected — 4h SL cooldown still active" — prompt-side
  compliance; the platform entry guard was never even attempted.
- **Stop worked**: with the OpenClaw gateway restarted, the harness stop
  reached Condor first try. No fourth wedge.

#### 9.5.2 Trading

Three positions, 0.02 SOL each (F4GpAFr6 tick 1, J8PSdNP3 tick 4,
4ko5tSr5/FEBU tick 8); F4GpAFr6 hit stop-loss by tick 5 (−0.0011 SOL).
Run PnL at last tick: **−0.0011 SOL**. Same buy-the-top exposure as runs
1–2; too short a run to say more.

#### 9.5.3 The bug: the post-shutdown sweep tried to SELL positions the policy said to KEEP

The `executors.jsonl` timeline for the two live positions:

```
23:22:58  CLOSING   ─ shutdown winddown (policy keep_spot_close_perp)
23:23:01  CLOSED    "stopped — holding 87.176906 units (detached)"   ✓ correct
23:23:39  ACTIVE    "early stop (closing position)"    ← resurrected at run_ended
23:23:48  CLOSING
23:23:54  FAILED    "max retries reached: Cannot send a request,
                     as the client has been closed."
```

Root cause chain (`condor/agents/lifecycle.py`, `shutdown` verb):

1. `engine._run_shutdown()` runs the deterministic winddown, which honors
   the policy — both **spot** positions were *detached* (kept, per
   `keep_spot_close_perp`) and reported `stopped=2, failures=0, verify=flat`.
2. The verb then unconditionally calls
   `runtime.stop_slug_executors(slug, keep_position=False)`
   (lifecycle.py:195) — a "leftovers" sweep that **ignores the policy**.
3. `stop_executor(keep_position=False)` deliberately *reactivates* detached
   positions ("explicit close reactivates that durable scope",
   runtime.py:172-179) — so the sweep resurrected the two just-detached
   records and issued sell orders **against the policy's keep decision**.
4. The sells failed only by luck: the run teardown had already closed the
   shared HTTP client. Had it been alive, the sweep would have liquidated
   positions the operator's policy said to keep.

Consequences, verified on-chain (public RPC, wallet `82Sg…yHx5`): both
token balances are still in the wallet (87.176906 J8PSdNP3…,
724.690566 4ko5tSr5…; F4GpAFr6 = 0 — its in-run SL sell was real), but the
durable records now read **FAILED** (state CLOSING) instead of
CLOSED-detached. That matters three ways:

- **Policy violation** — the sweep must pass `keep_position` per record
  (`_keep_position(record, policy)`), or skip records the winddown already
  handled; today it hardcodes `False`.
- **Record corruption** — FAILED is terminal, so `load_non_terminal`
  recovery, performance folds, and the SL-cooldown guard all now see a
  false FAILED where "detached, holding N units" was the truth. ~0.04 SOL
  of real inventory is invisible to recovery.
- **Silent** — the resurrection + failure happened *after* `run_ended`
  (7 ms after, same call stack epoch), so nothing appears on the run
  stream and no notification fired. The "✅ emergency shutdown complete"
  notification preceded the corruption.

**Fixed 2026-07-16** (all four layers, see ledger item 7):

1. *Policy* — lifecycle's shutdown verb no longer sweeps with
   `keep_position=False`: live engines already get the slug-wide,
   policy-scoped `run_shutdown`; with no live engine, the new
   `shutdown.winddown_slug(slug)` runs the same policy-resolved
   deterministic baseline + verify-and-retry, and notifies via the outbox
   (regression tests: `test_winddown_slug_honors_policy`,
   `test_winddown_slug_never_resurrects_detached_positions`).
2. *Stale object* — `stop_executor` now rebuilds an executor whose prior
   loop has ended from the durable record with a fresh connector
   (`_on_task_done` had closed the old one, and the adapter holds the same
   reference), so an *intentional* detach-reactivation close works
   (`test_stop_detached_position_reactivates_with_fresh_object`).
3. *Silence* — an executor loop that exits FAILED now alerts the operator
   through the notifications outbox, independent of `notify_trades` and of
   whether the owning run is still alive.
4. *The records* — a one-off `repair_run4_records.py` script appends one
   corrective event per executor (config + state copied from the genuine
   detached close, close_reason documenting the repair); the last-wins fold
   then reads CLOSED/COMPLETE/detached again, matching the verified
   on-chain truth. (Run by the operator — the harness is not permitted to
   mutate `executors.jsonl` directly.)

**Postscript (2026-07-17): the inventory was subsequently sold.** The
token-account transaction history settles the full timeline: the sweep's
23:23:48Z close attempts never reached the chain (the client-closed error
was pre-broadcast); the inventory sat detached-held for ~2.5 h (balance
check confirmed both amounts present); then both tokens were sold
**outside any executor** at 01:49Z (txs `3TtjDo2b…`, `4HvPJAT1…`) — by a
manual/other-session action, not by Condor's executor path (no executor
event exists for it) — 14 minutes *before* the v1 repair ran at 02:03Z. A
v2 correction (`repair_run4_records_v2.py`) replaces `close_type:
detached` with the inert `external_close` and records the sale txs, so the
books once again match a verified-flat wallet. A prior cross-session note
claiming "the close swaps actually landed" was wrong on causality — the
failed closes and the eventual sale were different transactions two and a
half hours apart.

#### 9.5.4 Secondary finding: `mutating` is always False, and the ACP risk gate never sees executor creates

Every `tool_call` event in run 4 says `mutating: False` — including the
three executor creates. `is_dangerous_tool_call` (gating.py:27) reads
`tool_call.get("tool") or tool_call.get("title")`, but the engine's folded
dict keys the name as `name` (client.py fold), so the audit flag can never
be True. On the live permission path the raw ACP `toolCall` *does* carry
`title`, but the action check reads `tool_call.get("input")` while ACP
sends arguments as `rawInput` — so `manage_executors` create resolves to
action `""` and the risk gate's early check (and experiment-mode
cancellation at that layer) silently no-ops. **The hard caps hold** —
`ops.create` enforces risk as a platform invariant on the only create
path — but the defense-in-depth layer is dead code under ACP key naming.

**Fixed 2026-07-16** (ledger item 8): shared `tool_call_name` /
`tool_call_input` helpers in gating.py resolve all three producer shapes;
every gate consumer (risk gate, deny/approval gates, confirmation summary)
uses them. Fail-closed hardening on top: arguments-unavailable
`manage_executors` calls classify as dangerous, `check_executor_action`
denies them ("failing closed"), and experiment mode cancels them. Covered
by `tests/test_gating.py` + three new shape tests in `test_risk_gate.py`.

---

## 10. Tradeoffs

### What the new model clearly wins

1. **One format, one writer, one truth.** The old system had ≥6 persisted
   formats (journal sections, snapshot files, meta.yml, experiment files,
   delegation transcripts, learnings categories) each with its own writer AND
   its own parser. Every parser was a crash surface — most damningly the risk
   engine reading exposure/drawdown out of regex-parsed markdown that two
   processes were concurrently rewriting. The new model has one append-only
   format and pure-function folds.
2. **Crash-safety is engineered, not incidental.** Per-event flush, fsync on
   financial events, torn-tail truncation on reopen, mid-file corruption
   raising loudly, and a recovery sweep that also voids orphaned permission
   approvals. The old atomic-rename + flock protected individual writes but
   couldn't express "this run ended by crash" beyond flipping a status string.
3. **Secrets and size are handled at the boundary.** Old snapshots persisted
   the entire system prompt and raw tool payloads every tick with no redaction.
   New events pass a redactor (sealed key names + secret-shaped value regexes)
   and a 16KB cap with hashed artifact spill.
4. **Identity is opaque.** Nothing parses `_eN` / `-dN` suffixes anymore; a
   whole class of grammar-drift bugs (the old code had a 17-file
   `controller_id` compat layer) is gone.
5. **Uniformity across kinds.** Sessions, experiments, delegations, consults,
   scheduled runs are one artifact with one lister, one exporter, one recovery
   path. Consults are even recorded now (they persisted nothing before).
6. **Risk decoupled from prose.** Exposure/open-count come from the executor
   store (venue truth); a markdown hiccup can no longer blind the risk engine
   while positions are open.

### What was genuinely lost

1. **Human-legibility at rest.** You could `cat` a session's journal and
   understand it. Now you need `render_run_markdown` (or jq). The larger half
   of this loss — the *exact prompt* the model saw — was recovered on
   2026-07-16 by persisting the prompt on `tick_started` (redacted, spilled
   to `.artifacts/`); run 2 has all 32 prompts on disk.
2. ~~Working-memory feedback broken~~ — **was true as shipped, fixed
   2026-07-16** (§7): the tick path now emits the `state`/`decision` keys the
   projection reads, and run 2's prompt artifacts show `[CURRENT STATUS]` and
   `[RECENT DECISIONS]` populated.
3. ~~Learnings channel orphaned~~ — **was true as shipped, fixed 2026-07-16**
   (§5.2): `record_learning` restores the explicit agent write path to
   learnings.md. What remains lost vs old: categories, promotion provenance,
   and the >50%-overlap auto-dedupe (now prompt guidance only).
4. **Bounded artifacts became unbounded streams.** Snapshots were capped at
   100 files; Decisions at 20 lines. A long-running session's `.jsonl` grows
   without bound (plus now ~20KB/tick of prompt artifacts), and `read_events`
   loads the whole file to project it — fine at 32 ticks, worth watching at
   10,000 (the 64KB-tail `list_runs` path is immune; the per-tick projection
   is not).
5. **History was amputated, not migrated.** All pre-refactor sessions,
   experiments, and delegations are gone (accepted, documented, irreversible).
   Track-record continuity restarts at zero.

### Judgement calls that are defensible either way

- **Full-prompt persistence**: resolved via the middle path — the prompt rides
  the existing artifact-spill mechanism (redacted, hashed, previewed), giving
  old-snapshot forensics without unredacted markdown. The remaining judgement
  call is retention (§9.4).
- **In-memory PnL/drawdown baseline per run**: the old drawdown series survived
  restarts via markdown; the new one deliberately resets ("engines are
  memory-only"). Cleaner semantics, but a crash-loop resets the drawdown
  kill-switch's memory each time; the events to rebuild the series exist in the
  stream if that ever needs tightening.
- **"Curation is the agent's job"**: removing categories/promotion/dedupe
  simplified ~1,000 lines of machinery, at the cost of betting prompt guidance
  will keep memory and learnings clean. Run 2 shows early duplicate pressure
  (§5.3, §9.1); a write-time near-duplicate warning would be cheap if the bet
  fails.

### Suggested follow-ups

The original four follow-ups (decision fold, learnings tool, state emission,
prompt persistence) all landed on 2026-07-16 and were validated by run 2. The
open list is now §9.4.

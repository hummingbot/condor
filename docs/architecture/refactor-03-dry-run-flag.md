# Refactor 03 — Dry run as a session flag, not a storage format

Status: **withdrawn** (2026-07-11) · Branch: `spike/simpler-agent-framework`

## 0. Decision: keep dry runs as-is

Considered and rejected. The existing dry-run structure — single tick, no
journal, flat `dry_runs/experiment_N.md` — is **intentional**: it lets a user
test and build an agent *without affecting it* (no learnings writes, no
session-history noise, no track-record pollution). With that behavior kept,
the storage merge below loses its rationale:

- A dry run inside `sessions/` would be a degenerate session (meta + one
  snapshot, no journal, no frozen config) — every session reader gains an
  "unless it's dry" conditional, roughly equal to the plumbing the merge
  deletes.
- The `sessions/` vs `dry_runs/` split is semantically load-bearing:
  sessions are the numbered, PnL-attributed operational track record;
  dry runs are disposable build artifacts. Merging them consumes session
  numbers during build iteration, forces perf-rollup filtering, and breaks
  "clear my test runs = delete one directory".

Salvageable micro-improvements (independent of any merge):

1. **Frontmatter over regex** — write `mode`/`model`/`error` as YAML
   frontmatter in `EXPERIMENT_TEMPLATE` instead of prose lines that
   `sessions_index._parse_experiment_file` regex-scrapes.
2. **The `run_once` wrinkle** — `run_once` trades *live* yet is stored in
   `dry_runs/` with no journal. If the boundary is "dry_runs = safe scratch,
   sessions = anything touching capital", `run_once` belongs on the sessions
   side (safety-based boundary, not duration-based).
   **Adopted into refactor-01 §4 (2026-07-11):** `run_once` becomes an
   ordinary tick session with `max_ticks: 1`; `is_experiment` narrows to
   `dry_run` alone, and `dry_runs/` holds only true dry runs.

The original proposal is preserved below as the record of the considered
design and its tradeoffs.

---

Original proposal (depends on
[refactor-01](refactor-01-agent-strategy-merge.md); interacts with
[refactor-02](refactor-02-unified-run-primitive.md) §4.1):

## 1. Goal

Kill the separate "experiment" artifact. A dry run becomes a **normal session
with `dry_run: true` in `meta.yml`** — same directory, same journal, same
snapshots, same config freeze, same UI surface — differing *only* in that
mutations are blocked and the prompt says so. `dry_runs/experiment_N.md`, the
`_eN` id namespace, and every `is_experiment` branch are deleted.

The stated goal: **let the user safely test a session without execution,
with everything else identical.** Today's dry run fails that bar in ways that
matter (§3).

## 2. What actually differs today: session vs experiment

Three orthogonal concerns are tangled into one `execution_mode` enum
(`loop | dry_run | run_once`, `config.py:51`):

```
axis          what it should control          what controls it today
-----------   ----------------------------    -------------------------------
SAFETY        are mutations allowed?          execution_mode == "dry_run"
DURATION      one tick or many?               execution_mode != "loop"  (+ max_ticks)
STORAGE       journal+snapshots or flat file? is_experiment = mode in (dry_run, run_once)
```

Storage follows **duration**, not safety — which produces the wrinkle that
exposes the tangle: **`run_once` trades live** (real executors, real PnL,
`_eN`-tagged) **yet is stored as an "experiment"** with no journal, while
`dry_run` — the actual safety mode — is *forced* to a single tick.

Full difference inventory (all of column 2 exists only because storage was
keyed on the wrong axis):

| | Session (`loop`) | Experiment (`dry_run` / `run_once`) |
|---|---|---|
| Id / counter | `{slug}_{N}`, `next_session_number` | `{slug}_e{N}`, separate `next_experiment_number` |
| Storage | `sessions/session_N/` (journal.md, frozen config.yml, snapshots/) | single flat `dry_runs/experiment_N.md` (`EXPERIMENT_TEMPLATE` ≈ snapshot template + `Mode:`/`Model:` header lines) |
| Journal | full protocol | `journal = None`; `journal_write` answers "skipped"; prompt forbids journal tools (`JOURNAL_SECTION_EXPERIMENT`) |
| Learnings readback | injected every tick | **none** — `learnings = "" if not journal` (`engine.py:356`) |
| Config freeze | `save_full_config` per session | not saved |
| Risk state | computed from own journal | `_NullTracker` zeros; `is_blocked`/`should_shutdown` both skipped |
| Perf rollup | fetched by `controller_id` | fetched, then hidden when `trade_count == 0` |
| UI / API | sessions endpoints | parallel experiments endpoints; `Mode:`/`Model:`/error flag **regex-parsed out of the markdown** (`sessions_index._parse_experiment_file`) |

## 3. Why flag-not-kind (and why the current dry run under-delivers)

A dry run is a **modifier**, not an invocation shape. `kind` (tick_loop /
delegation / consult) answers *how was this run invoked*; `dry_run` answers
*was it allowed to act*. Modeling it as a fourth `kind` would multiply
(`dry_tick`, `dry_delegation`, …); a flag composes:

```
                          DURATION (max_ticks)
                  1 tick          N ticks         unlimited
              +---------------+---------------+----------------+
  dry_run:    |  quick sanity |  bounded      |  standing      |   mutations
  true        |  check        |  rehearsal    |  simulation    |   blocked,
              |  (today's     |  (IMPOSSIBLE  |  (IMPOSSIBLE   |   full journal
              |   dry_run)    |   today)      |   today)       |
              +---------------+---------------+----------------+
  dry_run:    |  single live  |  bounded live |  normal loop   |   risk-gated
  false       |  tick (today's|  (max_ticks)  |                |   live trading
              |   run_once)   |               |                |
              +---------------+---------------+----------------+
```

The two "IMPOSSIBLE today" cells are precisely what "safely test a session"
means: run the *loop* — journal evolution, learnings accumulation,
tick-over-tick reasoning against the previous tick's summary — without
capital at risk, then review it with exactly the tools you'd use on a real
session. Today's dry run can't do any of that: one tick, no journal, no
learnings context, results in a bespoke file format. It tests the *prompt*,
not the *session*.

Concrete deficiencies fixed by "everything identical except execution":

1. **Dry runs can loop** — multi-tick simulated sessions become possible.
2. **Dry runs see learnings and their own journal history** — each tick reads
   what the previous simulated tick recorded, like a real session.
3. **Config is frozen per dry session** — a test is reproducible.
4. **Structured metadata** (`meta.yml`: `dry_run`, `model`, `status`) replaces
   regex-parsing `Mode:` lines out of markdown.
5. **One id namespace, one counter, one storage layout, one UI list** — the
   `_eN` universe and its parallel plumbing disappear.

## 4. Target design

### 4.1 Config: `execution_mode` is deleted

Two orthogonal fields replace the enum:

```yaml
# config.yml / launch config
dry_run: false        # SAFETY: block all mutating tool calls
max_ticks: 0          # DURATION: 0 = unlimited, 1 = single tick, N = bounded
```

| Legacy mode | Becomes |
|---|---|
| `loop` | `dry_run: false, max_ticks: 0` |
| `run_once` | `dry_run: false, max_ticks: 1` |
| `dry_run` | `dry_run: true, max_ticks: 1` |

The `{"dry_run": true}` shorthand the MCP tool already accepts
(`config.py:76`, `trading_agent.py:353`) stops being a translation and
becomes the literal config key. The `execution_mode` parameters on
`build_mcp_servers_for_session/_for_agent` (`_shared.py:328,416`) are
**dead** — accepted, never read — and are deleted outright.

### 4.2 Session layout: one shape for everything

```
agents/{slug}/
    sessions/
        session_N/
            meta.yml        # kind + dry_run + status/task/model/timestamps
            journal.md      # tick-loop sessions — dry or live
            config.yml      # frozen launch config — dry or live
            snapshots/snapshot_T.md
            transcript.md   # delegation / consult sessions
    # dry_runs/  ← gone
```

```yaml
# meta.yml
kind: tick_loop           # HOW it was invoked (unchanged from refactor-01)
dry_run: true             # WHETHER it could act (new, orthogonal)
status: stopped
model: claude-acp:sonnet
max_ticks: 3
started_at: ...
ended_at: ...
```

A dry tick session is byte-for-byte a tick session — journal protocol,
snapshot-per-tick, frozen config — created, listed, browsed, and reviewed
through the same endpoints, with a 🧪 badge sourced from `meta.yml` instead
of `ModeBadge` inferring from `_eN` ids and parsed markdown.

### 4.3 Behavior deltas keyed on the flag (the *only* deltas)

- **Permission policy**: the dry-run blocks currently inside
  `auto_approve_with_risk_check` (keyed on `execution_mode == "dry_run"`,
  `risk.py:225-244`) key on the flag instead. In refactor-02 terms:
  `risk_gate(..., dry_run=True)` cancels every mutating call. Because it's a
  policy modifier, `dry_run` composes with *any* kind in principle — a future
  "test this delegation safely" costs nothing extra. (Not wired now; noted as
  a payoff of flag-not-kind.)
- **Prompt framing**: `BASE_PROMPT_DRY_RUN` (observation-only, conditional
  language, 🧪 prefix) keyed on the flag. `JOURNAL_SECTION_EXPERIMENT` is
  **deleted** — dry sessions use the live journal protocol with one added
  line: journal what you *would* have done as the action entry.
- **Risk**: state is computed from the session's own journal exactly like a
  live session (exposure stays zero naturally — creates are blocked, so
  `track_executor` never fires). `should_shutdown` escalation is skipped when
  `dry_run` — there are no positions to wind down. The fail-closed
  `is_blocked` path (journal unreadable) applies to both.
- **Performance**: rollups skip sessions with `dry_run: true` in meta (no
  backend fetch at all — cleaner than today's fetch-then-hide). Live
  `max_ticks: 1` sessions now correctly appear as ordinary sessions with
  ordinary `_N` PnL attribution, instead of masquerading as experiments.
- **Journal tools just work.** The `journal_write` → `"skipped: experiment
  mode"` and `journal_read` → experiment-file special cases
  (`trading_agent.py:436-483,530-551`) are deleted, not ported.

### 4.4 Learnings from dry runs

Dry sessions **read** `learnings.md` like everyone (that's the simulation
being faithful) and **write** with automatic provenance: entries appended
from a dry session get a ` [dry]` suffix, so a future live session (and the
user) can weight simulated insights appropriately. Market observations from a
dry run are real observations — the market data was live — so suppressing
writes entirely would discard the main analytical value of a rehearsal;
execution-category learnings are naturally rare in dry runs since nothing
executes. One line of code in `append_learning`, keyed on the flag.

## 5. Code change inventory (mostly deletions)

| File | Change |
|---|---|
| `condor/agents/engine.py` | Delete `is_experiment`, the dual `__post_init__` branch (every run allocates a session dir + journal), the `save_experiment_snapshot` path in `_tick`, and `_NullTracker`. Single-tick self-stop keys on `max_ticks` alone (`>=1` covers old run_once/dry_run). Shutdown guard becomes `if should_shutdown and not cfg.dry_run`. |
| `condor/agents/journal.py` | Delete `save_experiment_snapshot`, `EXPERIMENT_TEMPLATE`, `next_experiment_number`. `append_learning` gains the `[dry]` provenance suffix param. |
| `condor/agents/sessions_index.py` | Delete `EXPERIMENT_DIRNAMES`, `count_experiments`, `list_experiments`, `_parse_experiment_file`, `_experiment_info_cache`, `find_experiment_file`, and the experiment branch of `enumerate_agent_ids`. `list_sessions` surfaces `dry_run` from `meta.yml`. |
| `condor/agents/config.py` | `execution_mode` field → `dry_run: bool` + existing `max_ticks`; `from_dict` shorthand translation deleted (the key is now literal). |
| `condor/agents/prompts.py` | Delete `JOURNAL_SECTION_EXPERIMENT` and the `is_experiment` parameter; select `BASE_PROMPT_DRY_RUN` and tool-preload omissions on the flag; `trading_agent_journal_write` always preloaded. `run_once` note generalizes to a `max_ticks`-remaining note. |
| `condor/agents/risk.py` (or `policies.py` post-r02) | Dry-run blocks keyed on `dry_run: bool` instead of `execution_mode` string. |
| `mcp_servers/condor/tools/trading_agent.py` | Delete `_resolve_experiment_file` and both experiment special-cases in `journal_read`/`journal_write`; `start_agent` docstring documents `dry_run` + `max_ticks` instead of `execution_mode`. |
| `handlers/agents/_shared.py` | Delete the dead `execution_mode` parameters. |
| `condor/web/routes/agents.py` | Delete the two experiments endpoints and `ExperimentInfo`; `SessionInfo` gains `dry_run: bool`; perf computation filters dry sessions before fetching. |
| `frontend/` | Experiments tab merges into the sessions list with a 🧪 badge + filter; `ModeBadge`/`modeStyles`/`parse-agent.ts`/`agentStatus.ts` derive mode from `meta` fields (`dry_run`, `max_ticks`) instead of `_eN` ids and `execution_mode` strings; delete experiment API helpers in `api.ts`. |
| tests | Dry sessions produce journal + snapshots; learnings `[dry]` tagging; perf skips dry sessions; `max_ticks` self-stop for both dry and live; migration script. |
| docs/skills | `agent_builder` SKILL dry-run step now reads: launch with `config={"dry_run": true, "max_ticks": 1}` and review the session like any other. |

## 6. Migration

Extends refactor-01's script (or a follow-up `migrate_dry_runs.py` if r01 has
already run):

1. For each `dry_runs/experiment_N.md`: allocate the next session number,
   create `sessions/session_M/`; the file becomes
   `snapshots/snapshot_1.md`; synthesize `meta.yml` from the parsed header —
   `kind: tick_loop`, `dry_run:` true iff `Mode: dry_run` (a `run_once` file
   gets `dry_run: false, max_ticks: 1`), `model:` from `Model:`, `status:
   error` iff the error pattern matches; synthesize a minimal `journal.md`
   (template + one tick line) so readers never special-case a missing journal.
2. Remove `dry_runs/`.
3. Preserve nothing in place: after this, no code path reads `experiment_*.md`
   or `_eN` ids.

Data on this machine: 5 experiment files across 2 agents. Historical `_eN`
executor tags in the Hummingbot backend (from past live `run_once` ticks)
stop being enumerated — same acceptance as refactor-01's id change, zero
impact on this checkout.

## 7. Tradeoffs & edge cases

- **Unbounded dry loops burn tokens silently.** Today `dry_run` implies one
  tick; after the split, `dry_run: true` with `max_ticks: 0` runs a
  simulation forever at real LLM cost. Per the clear-errors rule, **require
  explicit `max_ticks ≥ 1` when `dry_run: true`** — `start_agent` errors
  loudly rather than defaulting. (A user who genuinely wants a standing
  simulation passes a large bound deliberately.)
- **Dry sessions inflate the sessions list.** They're now first-class
  citizens of the same list; the 🧪 filter handles browsing, but retention
  (e.g. keep last K dry sessions) should ride along with the consult-session
  retention decision from refactor-01 §10.1 rather than growing a third
  policy.
- **The flat file was skimmable.** One `experiment_N.md` was a single `cat`;
  a session is a directory. Cost accepted: a single-tick session is exactly
  `meta.yml` + one snapshot + a two-line journal, and it buys uniform
  tooling. The web/MCP session readers are the intended review path anyway.
- **Learnings provenance is a heuristic.** The `[dry]` suffix keeps the
  shared `learnings.md` honest but dedup now compares across dry/live
  variants of the same insight — acceptable (dedup is fuzzy word-overlap
  already, and a dry duplicate of a live learning *should* dedup away).
- **`status` in meta must reflect single-tick completion** (`stopped` after
  the self-stop, `error` if the tick's model call failed) so the UI's error
  badge survives the loss of regex detection — the engine knows this
  directly and writes it structurally, which is strictly more reliable.

## 8. Sequencing

After refactor-01 (needs the `sessions/` + `meta.yml` envelope). Order
against refactor-02 is flexible but **r03-before-r02 is slightly better**:
r03 deletes `execution_mode`, which r02's `risk_gate(engine, state,
execution_mode)` signature would otherwise carry briefly and then re-touch.
Either way the overlap is one function's keying (string mode → bool flag).

## 9. Open decisions (recommendations inline)

1. **Require explicit `max_ticks` for dry runs?** *Recommend yes* (§7) —
   silent unbounded simulation cost is the one new foot-gun this refactor
   introduces, and a loud error at start is cheap.
2. **`[dry]` learnings suffix vs suppressing dry learnings entirely?**
   *Recommend the suffix* (§4.4) — market observations from rehearsals are
   real signal; provenance beats suppression.
3. **Expose `dry_run` for delegations now?** *Recommend later* — the flag
   composes with the refactor-02 policy lattice for free when a concrete
   need appears (e.g. rehearsing a deployment delegation); wiring it now
   adds UI/docs surface without a user.

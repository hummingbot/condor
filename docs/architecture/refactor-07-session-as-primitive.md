# Refactor 07 — consult / delegate / dry_run / session: four verbs, four artifacts

Status: **proposed** (2026-07-11) · Amends the implemented
[refactor-01b](refactor-01b-agent-history-multi-strategy.md) storage model;
leaves [refactor-02](refactor-02-unified-run-primitive.md)'s execution core
and [refactor-05](refactor-05-skill-evolution.md)'s curation loop intact.

## 1. The problem with "everything is a session"

Refactor-01b made `sessions/session_N/` the universal envelope: tick loops,
delegations, consults, and later curation all landed there, distinguished by
`meta.yml: kind`. That bought uniform analyzability — and immediately
required a pile of compensating machinery to undo its side effects:

- **kind filtering everywhere**: perf rollups filter `kind == tick_loop`;
  the web splits sessions into two tables by kind; `sessions_index` grew a
  `TICK_KIND` concept and kind-aware counting.
- **retention per kind**: consult sessions capped at 20, curation at 10 —
  pruning machinery that exists only because Q&A traffic was writing into
  the track record's namespace.
- **numbering pollution**: session numbers interleave kinds, so an agent's
  trading track record reads session 2, 9, 17 — a cosmetic cost we accepted
  and users immediately notice. Today's disk proves the point: across five
  agents there is exactly **one** tick session and thirteen
  delegation/consult/curation sessions. The "session list" is 93% not
  sessions in any meaningful sense.
- **semantic overload**: "session" stopped meaning anything. A session that
  answered "what's SOL funding?" and a session that ran capital under risk
  limits for a week are the same noun.

The uniformity was the wrong axis. The distinction that matters is not
*how the brain was invoked* but **whether state and capital attribution
accumulate**.

## 2. The re-carve: four primitives, four artifacts

A user invokes an agent's brain in exactly four ways — in order of
increasing commitment — and each leaves exactly one kind of artifact:

| Verb | Question it answers | Stateful? | Gate | Artifact |
|---|---|---|---|---|
| **consult** | "what do you think?" | No | human_gate (fail-closed) | the **answer**, returned inline — nothing on disk |
| **delegate** | "go do this, tell me when done" | No (one-shot) | risk_gate (zero-seeded) / AUTO | a **transcript** — flat file, `activity/delegations/` |
| **dry_run** | "would these orders work?" | No (one rehearsal tick) | risk_gate(`dry_run=True`) — **every** mutation cancelled | a **snapshot** — flat file, `activity/dry_runs/` |
| **session** | "engage capital under these orders" | **Yes** | risk_gate (journal-seeded) | a **journal** — `sessions/session_N/` with config, snapshots, meta |

(Curation is the fifth invocation shape, but the *framework* invokes it, not
the user — it's housekeeping, not a verb in the user's vocabulary. Its
transcript lands in `activity/curation/`.)

**Session — not tick — is the stateful primitive.** A session is the unit
of stateful engagement: frozen config, journal, risk state carried across
invocations, `controller_id` attribution, a place in the track record.
*Ticking* is demoted to what it actually is — the scheduler's way of
advancing a session — not the identity of the thing. (`max_ticks: 1` "run
once" reads naturally now: a session advanced exactly once.)

**dry_run is already carved correctly** — verified against the code, it is
the one invocation shape 01b never sucked into the session envelope:

- `engine.py __post_init__`: `execution_mode == "dry_run"` sets
  `is_experiment`, allocates **no** session dir and **no** journal; the id
  is `{slug}_e{N}` from `next_experiment_number()` (its own counter — never
  consumed a session number).
- `_loop()`: one tick, then self-stop. `_maybe_curate` skips experiments.
- `risk.py _risk_gate_callback(dry_run=True)`: cancels every mutating
  action, so nothing ever reaches the exchange — which is why the `_eN` id
  never becomes on-exchange identity and dry-run files are freely movable
  (unlike session numbers).
- Artifact: one flat `experiment_N.md` via `save_experiment_snapshot()` —
  prompt, tool calls, risk state, response.

So refactor-07 doesn't change dry_run's semantics; it *recognizes* them.
dry_run was the existing proof that "one-shot invocation → flat file" is
the right shape, and delegations are being restored to it.

**run_once is a session — verified.** `__post_init__` normalizes
`execution_mode: run_once` → `loop` + `max_ticks: 1` *before* the
experiment check and session allocation, so a run_once gets a real
`sessions/session_N/`: frozen config, journal, meta, baseline-seeded risk,
`controller_id` attribution, finalize + curation trigger on stop. It is a
session advanced exactly once — in the track record, as it should be
(run_once can place real orders).

This also restores the word to its business meaning: the tokenizable /
auditable unit with a track record is the session, full stop
(`docs/strategy/business-strategy.md` §11a's conclusion, which 01b's
overload had muddied).

`run_agent` + the policy lattice are untouched: all three verbs remain call
sites of the same execution core. This refactor moves *artifacts*, not
execution.

## 3. What each verb leaves behind (target design)

The agent dir splits into definition (`AGENT.md`, `skills/`, `routines/`,
`strategies/`), memory (`learnings.md`, `store/`), **track record**
(`sessions/` — the stateful primitive, top billing), and **activity log**
(`activity/` — every one-shot invocation, one flat file each):

```
agents/{slug}/
    AGENT.md  learnings.md  shutdown.md  skills/  routines/  store/
    strategies/{sslug}/strategy.md
    sessions/                        <- ONLY real sessions. Gapless numbering
        session_N/                      going forward; every entry is track
            meta.yml                    record. No `kind` field needed —
            config.yml                  meta = strategy, status, model,
            journal.md  snapshots/      timestamps, controller_id.
    activity/                        <- one-shot invocations, one flat file
        delegations/                    each, named {date}-{id}.md — date-
            2026-07-11-d3.md            first so ls sorts chronologically;
        dry_runs/                       the short id (d3 / e2 / c1) stays
            2026-07-11-e2.md            the lookup handle (glob *-d3.md).
        curation/                       No type prefix in filenames — the
            2026-07-11-c1.md            subfolder already says the type.
```

- **Sessions stay OUT of `activity/`** — deliberately. A session isn't a
  one-shot activity (a *tick* is; a session is the thing many ticks
  advance), and session dirs can't adopt the date-name convention anyway:
  `session_N` names are identity (`controller_id` tags on the exchange
  reference `{slug}_{N}`, and MCP/journal handles derive the path from the
  id). Keeping them top-level avoids one subfolder silently violating the
  naming rule — and gives the product (the track record) top billing.
- **Delegation transcripts** (`activity/delegations/{date}-d{N}.md`): header =
  status, task, risk_limits, started/ended; body = full transcript +
  result. Written AT START with `status: running` and rewritten on
  completion — keeps the crash-husk property without session machinery.
  Ids get their own namespace again (`{slug}-d{N}`, monotonic per agent)
  and stop consuming session numbers.
- **Dry-run snapshots** (`activity/dry_runs/{date}-e{N}.md`): same content as
  today's `experiment_N.md`; ids stay `{slug}_e{N}` (they're embedded in
  `split_agent_id` and the MCP read path). Only the file's home and name
  change.
- **Curation transcripts** (`activity/curation/{date}-c{N}.md`): keep last
  ~10. The real audit trail stays the scoped git commit.

- **Consult persists nothing.** The answer returns inline; done. *Optional
  mitigation for the one real loss (see §5): a single rolling
  `consults.log` per agent — timestamped task + answer lines, no
  transcripts, no numbering — is a cheap opt-in if the record proves
  missed. Not part of the base design.*
- **Curation** keeps its trigger, lock, tool profile, mandates, provenance
  stamps, and git commit exactly as implemented; only its transcript's home
  moves into `activity/curation/`.

## 4. Code change inventory

- `journal.py`: `allocate_session_dir` and meta helpers now serve sessions
  only; drop `prune_sessions` kind machinery (curation keeps a simple
  keep-last-K on its flat dir); `resolve_agent_dirs` unchanged
  (`{slug}_{N}` still resolves — and now always means a real session).
- `sessions_index.py`: **shrinks** — no `kind`, no `TICK_KIND`, no
  kind-aware counting; `list_sessions`/`enumerate_run_ids` lose their
  filters; `infer_latest_session_status` loses the kind check.
- `delegate.py`: back to flat-file persistence (write header at start,
  full transcript in `finally`); registry/notify/policy resolution
  (baseline/override, loud error) unchanged; `risk_limits` recorded in the
  file header instead of meta.yml.
- `consult.py`: delete `_persist_consult_session` + retention constants.
- **dry_run — path/name only, zero semantic change**: `journal.py`
  `save_experiment_snapshot` + `next_experiment_number` write/scan
  `activity/dry_runs/*-e{N}.md`; `sessions_index.py` `_EXPERIMENT_FILE_RE` +
  its globs update; `trading_agent.py` `_resolve_experiment_file` resolves
  `{slug}_eN` by glob (`*-e{N}.md`). Engine, gate, ids untouched.
- `curation.py`: transcript path changes; `keep-last-K` prune of the flat
  dir. One design point to settle: the curator's `promote_learning` calls
  currently authenticate with the curation *session id* — with no session,
  the journal tools accept the bare agent slug for **agent-level**
  operations (learnings live at the agent; a session handle was never
  semantically required). `journal_write` gains that path; loud error if a
  bare slug is used for session-scoped entry types.
- `engine.py`: unchanged except vocabulary (docstrings; optionally rename
  `TickEngine` → `SessionEngine` — cosmetic, defer).
- Web/MCP: sessions list is now only sessions (the "Delegations & Consults"
  panel reads the delegation registry + `delegations/` dir instead);
  `delegate(action="get")` unchanged; perf code drops its kind plumbing.
- Frontend: `SessionInfo.kind` disappears; the background-runs panel
  switches data source.
- Tests: consult-persistence and kind-filter tests removed; delegation
  flat-file tests restored (main's shape, plus the start-husk); curation
  tests re-pointed.

Net: **negative LOC.** This deletes more compensating machinery than it
adds (kind filters, per-kind retention, kind-aware indexes, the web split).

## 5. What we give back, honestly

1. **Consult records.** Today's design saved the day once already: the
   backtest_lab verdict outlived its HTTP client and survived only in the
   persisted consult session. Under this design that answer would have been
   lost. Mitigations: the rolling `consults.log` option (§3), and noting
   that long consults are delegation-shaped anyway — the router's own rule
   (">1-2 min → delegate") already points the durable path.
2. **Uniform session analytics.** "Show me everything this agent did" is no
   longer one list — it is `sessions/` + `activity/`. Softened by the
   grouping: one glob (`activity/**/*.md`) covers every one-shot
   invocation, and the agent detail page still shows everything; they're
   just two sources instead of one.
3. **meta.yml queryability for delegations** (status/task as YAML) becomes
   header-parsing of flat files. Acceptable at current scale; revisit only
   if delegation analytics become a real need.

What we get: session means something again; gapless track-record numbering
going forward; less machinery; the four verbs finally map one-to-one onto
four artifacts a user can name — and the agent dir reads as definition /
memory / track record / activity log.

## 6. Migration

Small and safe today (verified on disk: exactly **one** tick session
exists, `funding_rate_watcher_2`, which placed no orders; thirteen
non-tick sessions across five agents):

1. Back up `agents/` (same preflight as 01b).
2. For every `sessions/session_N` with `kind: delegation|consult|curation`:
   move `transcript.md` + meta header → `activity/delegations/{date}-d{K}.md` /
   `activity/curation/{date}-c{K}.md` (consults: drop, or fold into
   `consults.log` if the option is adopted); delete the session dir. Dates
   come from the meta's `started_at`.
3. Move `dry_runs/experiment_N.md` → `activity/dry_runs/{date}-e{N}.md` —
   **numbers preserved** (they're the `{slug}_eN` ids), date from the
   snapshot header. Safe to move freely: dry runs cancel all mutations, so
   `_eN` ids never became on-exchange identity.
4. **Never renumber surviving tick sessions** — `controller_id` tags on
   the exchange reference `{slug}_{N}`; numbers are identity. Legacy gaps
   in the low numbers remain as history; numbering is gapless from here on.
5. Drop `kind:` from surviving tick-session metas.

## 7. Open decisions

1. `consults.log` opt-in: recommend **defer** — ship without it, add if the
   loss is felt.
2. `TickEngine` → `SessionEngine` rename: recommend defer (pure churn).
3. MCP verb names (`start_agent` → `start_session`?): recommend
   `start_session` as an alias-free rename at implementation time — the
   vocabulary is the point of this refactor.
4. dry_run's entry point: today it rides `start_agent` as
   `execution_mode: "dry_run"`. Under a `start_session` rename that
   reads wrong (a dry run is precisely *not* a session). Recommend
   surfacing it as its own verb/action (`dry_run`) at the MCP/web layer at
   implementation time, even if the engine keeps the mode flag internally.
5. Delegation transcript cap: recommend none for now (they are the durable
   task record); curation keeps last 10.

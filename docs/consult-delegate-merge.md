# Run vs Consult vs Delegate: verb boundaries and what each stores

A decision doc for two coupled questions from the primitive audit
(2026-07-17):

1. **Surface**: delegation has never been used on this box, and most of its
   machinery duplicates the run store — keep it a separate primitive
   (**A**), or keep the verb but delete the parallel lifecycle underneath
   (**C**)? (An earlier draft weighed a third option — collapse
   consult+delegate into one verb with a `wait` flag — and rejected it; see
   "Why not a flag" for the one-paragraph residue.)
2. **Model boundary**: should the *strategy* portion of an agent's brain
   move out of `AGENT.md` (into a `strategy` file/block with default
   configs), so that **runs use agent + strategy** while **consult/delegate
   use agent only** — making the mandate/task split structural rather than
   behavioral?

The two questions share one root: the chat brain must route a human's
spoken intent across the whole verb space, and the primitives must be
shaped so it routes right on most utterances.

## Ground truth: who actually calls these verbs

Nobody types `consult(...)`. The caller is almost always the **main condor
chat agent** (or another MCP host) — an LLM choosing tools from its routing
instructions. The human speaks intent; the LLM picks the verb. So "API
ergonomics" here means **prompt-routing ergonomics**: how reliably does an
LLM map an utterance to the right call with the right authorization?

The full verb space the chat brain faces (not just consult/delegate):

| User says to chat | Chat brain does today | Waits? | Who authorizes trades |
|---|---|---|---|
| "run the trender" / "start the MM agent" | `run_agent(slug)` → **session** | no — it's the agent's own loop | risk gate seeded from the agent's journal |
| "test it first / dry-run it / without real money" | `run_agent(slug, dry_run=true)` → **experiment** | no | nobody — every mutation blocked |
| "run it focused on SOL, 10 ticks" | `run_agent(slug, trading_context=…, max_ticks=10)` → session | no | journal-seeded risk gate |
| "what's wrong with my perp positions?" | `consult(agent="executor_manager", …)` | yes | human, per trade (approval queue) |
| "have the MM agent rebalance now" | `consult(agent="perp_market_maker", …)` | yes | human, per trade |
| "build a routine that scans SOL pools, ping me" | `delegate(action="start", agent="routine_builder", …)` | no | n/a (non-trading → auto) |
| "unwind those positions, cap it at $500, ping me" | `delegate(action="start", …, risk_limits={…})` | no | nobody — the caps ARE the authorization |
| "how's that background task?" | `delegate(action="get", task_id=…)` | — | — |
| "stop it" | `delegate(action="stop", task_id=…)` | — | — |

## The two axes that separate all four modes

Every utterance resolves on two axes:

|  | **Attended** (user present, will answer) | **Unattended** (user leaves) |
|---|---|---|
| **The agent's own mandate** (its strategy) | — (you don't babysit a tick loop) | **session** — journal-seeded risk; **experiment** when rehearsing |
| **A caller-specified one-off task** | **consult** — human gate per trade | **delegate** — zero-seeded budget gate |

Two litmus tests resolve the gray zones:

- **Whose goal?** "Run the MM agent" names no novel goal → mandate →
  session. "Have the MM agent unwind my ETH" names a one-off goal →
  consult/delegate — even though the same agent executes it.
  `trading_context` does NOT flip this: "run it focused on SOL" steers the
  mandate; it doesn't turn it into a task.
- **Completion shape.** Work that finishes by itself ("until done": build,
  scan, unwind, produce) → consult/delegate. Work that runs until stopped
  or bounded ("until I stop it / N ticks") → session.

## What each verb stores — the concrete answer

All four kinds write the **same** append-only run stream:
`agents/{slug}/runs/{ulid}.jsonl` — `run_started`, `tool_call` events,
`run_ended` — and the startup sweep closes crashed ones as `interrupted`.
That is the shared substrate. The difference is what each writes *on top*:

| | consult | delegate | session (run) | experiment (run) |
|---|---|---|---|---|
| Run stream | ✅ `kind=consult` | ✅ `kind=delegation` | ✅ `kind=session` | ✅ `kind=experiment` |
| `run_started` payload | `{task, model, account_ref?}` | same | **FrozenSpec** (below) | FrozenSpec |
| Frozen spec | none | none | ✅ model, denomination, **merged config**, tools, schedule, account, source+resolved hashes | ✅ (mutations blocked) |
| Journal / drawdown carry-over | none | none | ✅ the stateful capital record | ✗ rehearsal — excluded from the track record |
| Track-record entry (session #) | ✗ | ✗ | ✅ | ✗ |
| Return channel | inline answer to caller | outbox ping when done | — (scheduler/loop advances it) | flat snapshot |
| Extra live state | none | in-memory `DelegateTask` (asyncio handle + notify hook), dies with process | live `TickEngine` in `_engines` | live `TickEngine` |
| Identity | run_id | `task_id` **(= run_id, renamed)** | run_id | run_id |

The load-bearing rows are **frozen spec**, **journal**, and **track
record**: a session freezes and executes the agent's `default_config` as a
loop, seeds its risk gate from prior drawdown, and appends to the agent's
performance history. Consult and delegate do **none** of that — they take a
one-off task and leave only the stream.

But that is a difference in *what loops and what's booked*, **not** a clean
"consult/delegate ignore `default_config`" — which is where an earlier
draft of this doc was wrong. Consult resolves its trading account *from*
`default_config`, and a consult/delegate that opens a position runs through
the exact same guard and cap machinery a session does. Question 2 only
makes sense once we see precisely which parts of the config each verb
touches, and how the guards/risk differ (or don't).

## How entry_guards and risk policy actually apply per verb

Before deciding what to split, pin down the mechanism — because the naive
"default_config is run-only" is false. There are **three enforcement
layers**, and only one of them varies by verb:

1. **Permission gate — the ONE per-verb difference.** The
   `permission_policy` passed to `run_agent`:
   - consult → `human_gate` — dangerous calls (executor create/stop) go to
     the approval queue; a human decides.
   - delegate → `risk_gate` seeded at **zero** — auto-approve *within the
     caps the caller passed* (caps = a per-run budget); non-trading agents
     get full auto.
   - session → `risk_gate` seeded from the **journal** — auto-approve
     within caps, with prior drawdown carried in.
   - experiment → block **every** mutation.
2. **entry_guards — identical across all three.** `entry_guard_reason` is
   enforced inside the `manage_executors` tool (the only create path the
   agent brain calls), reading `agent.default_config.entry_guards`. It fires
   whenever *any* agent brain — session, consult, or delegation — tries to
   open an executor. The code comment says why: "the model does not hold
   such rules reliably across ticks", so it's enforced in code, not left to
   the gate. **No verb difference.**
3. **Platform risk caps — same mechanism, per-verb what's frozen.**
   `_enforce_agent_caps` runs on the only create path for any `origin=agent`
   capability. It composes the **agent baseline** (`AGENT.md risk_limits`,
   always) with the **frozen run limits** (stricter, if the verb set any).
   Session freezes stricter caps from launch config; delegate injects the
   per-call `risk_limits` as its budget; consult passes **none**, so only
   the baseline applies (the human gate is its real control). Same enforcer,
   different run-limit input.

So to the direct question — *how do entry_guards and risk policy differ
between consult, delegate, and run?*

| | consult | delegate | session | experiment |
|---|---|---|---|---|
| entry_guards | same (tool-layer) | same | same | same (moot — no trades) |
| agent risk baseline cap | enforced | enforced | enforced | moot |
| run-level caps | none passed | the per-call budget | frozen stricter | moot |
| permission gate | human, per trade | zero-budget auto | journal-seeded auto | block all |

**entry_guards don't differ at all. The risk *baseline* doesn't differ. Only
the gate — who says yes — and the run-cap seed differ.** That difference is
behavioral (which policy object), not config a file split could relocate.

## Question 2: should strategy move out of AGENT.md?

### `default_config` is not one thing — it's four, by scope

The proposal "runs use agent+strategy, consult/delegate use agent" assumes
`default_config` is a coherent run-only block. It isn't. Sort its keys by
*who needs them*:

| Group | Keys (memecoin_trender) | Used by |
|---|---|---|
| **Operating context** | `venue`, account/denomination | **every** trading verb — a consult that opens a position needs to know which account/venue |
| **Guardrails** | `entry_guards`, `risk_limits` | **every** agent create (layers 2–3 above) — not run-only |
| **Executor tactic defaults** | `take_profit_pct`, `stop_loss_pct`, `time_limit_s`, `amount_quote`, `min_liquidity_usd` | the brain, **whenever it opens a position** (any verb) — and, as you note, these are **position-executor-only**: an `order_*` executor has no tp/sl, so they aren't even a uniform "strategy block" |
| **Loop cadence** | `frequency_sec`, `execution_mode`, `max_ticks` | **`run_agent` only** — a consult/delegate is one invocation, it never loops |

Only the **last group is cleanly run-only.** Operating context and
guardrails are needed by any verb that trades; tactic defaults are drawn on
by the brain in any verb that opens a position (and are executor-type-
specific, so they don't even form one block). The prose body is expertise —
also shared (the "what good momentum looks like" knowledge is exactly what a
consult needs).

That kills the clean file split. If `strategy.md` = "everything a run uses,"
it swallows venue/account/guards that consult/delegate also need — you'd
either duplicate them or make consult read `strategy.md` too (at which point
it isn't strategy-only). If `strategy.md` = "the cleanly run-only part," it
holds three cadence fields — not worth a file.

### The corrected boundary

The mandate/task split is **behavioral, not structural**. What actually
separates a run from a task is not a slice of config — it is:

- **the permission gate** (journal-seeded auto vs human vs zero-budget),
- **the loop** (cadence fields + continuous execution vs one invocation),
- **the books** (journal, drawdown carry-over, track-record entry).

None of those three relocate to a file. `default_config` is shared
infrastructure with one run-only corner (cadence); the prose is shared
expertise. So:

- **S0 — status quo.** Keep `default_config` a block in `AGENT.md`. The one
  real defect is a **leak the other way**: consult resolves its account
  from `default_config.venue`, coupling a task path to loop config. That's a
  bug to fix regardless (account/venue should resolve from the agent's
  `denomination`/`account`), but note the direction — the task path reaches
  *into* config, it doesn't get to *ignore* it.
- **S2 — split a `strategy.md` file.** Rejected for now: the config doesn't
  cleave along "run vs task" (three of four groups are shared), so the file
  would either duplicate shared keys or pull consult/delegate into reading
  it. It also re-introduces the entity §5.3 deliberately collapsed. Its only
  genuine payoff — **multiple named strategies per agent / config
  versioning** — is real but unneeded today (no agent has >1 strategy).

### Recommendation on question 2

**Keep one file. Do not split `strategy.md`.** The mandate/task boundary the
user wants sharpened is already carried by the verb (gate) and the run
lifecycle (loop + books), not by config layout — and the config does not
partition cleanly enough for a file to help. Two concrete cleanups make the
existing boundary honest without new structure:

1. **Fix the venue leak** — resolve a trading account from the agent's
   `denomination`/`account`, never from `default_config.venue`, on every
   path. Removes the one place a task reaches into loop config.
2. **Name the cadence group** — mark `frequency_sec`/`execution_mode`/
   `max_ticks` as the run-only sub-block (even just a `loop:` nesting or a
   doc note), so it's visible that these are the only keys a task ignores.

Promote to a real `strategies/<slug>.md` **only** on a concrete trigger: an
agent that needs more than one strategy, or config versioning/A-B. That —
not mandate/task clarity — is the need a separate strategy entity actually
serves.

## Question 1: the delegate surface — A vs C

### Design A — status quo (two verbs, two lifecycles)

| Layer | consult | delegate |
|---|---|---|
| MCP tool | `consult(agent, task, context)` | `delegate(action=start\|list\|get\|stop, agent, task, task_id, risk_limits)` |
| Control methods | `agent.consult` | `delegate.start` + `.list` + `.get` + `.stop` |
| Identity | run_id | `task_id` (= run_id, renamed) |
| Tracking | run stream | run stream **+** in-memory registry |
| Stop | `agent.verb stop` | `delegate.stop` |
| Code | `consult.py`, 114 lines | `delegate.py`, 317 lines |

`delegate.list`/`get`/`stop` re-answer questions `list_runs`/`get_run`/
`control_run` already answer against the same ULIDs — `delegate.get` even
has an explicit "fall back to RunStore" branch. `agent.list` does NOT show
delegations (they aren't engines), so today enumerating "what's running"
takes two tools, and `condor status` shows live sessions but not live
delegations.

**Benefit of A:** the authorization difference (human-gated vs
budget-gated) is carried by the *verb name* — the discrimination task LLMs
do best, and it stays visible in every transcript line.

### Design C — thin verbs (keep the names, delete the plumbing)

```
consult(agent, task, context)              → unchanged: blocks, human gate
delegate(agent, task, risk_limits={…})     → start-only: {"run_id", …};
                                             zero-seeded gate; ping on done
```

- `delegate` loses `action`/`task_id`; its start response's `next_steps`
  points at `get_run(run_id)` / `control_run(run_id, "stop")`.
- Delete `delegate.list`/`get`/`stop` control methods; keep
  `delegate.start` (internally may be a param on `agent.consult`).
- `DelegateTask` shrinks to a notify-on-done hook; the `delegation` run
  kind stays as metadata so `condor runs --kind delegation` still works.
- `task_id` vocabulary gone — start returns `run_id`, like every other
  tool.

**Benefit of C:** keeps A's decisive property (authorization stays in the
verb name) while deleting the shadow lifecycle, the second id, and the
two-source "what's running?" problem. Track-side questions route through
the same run tools the chat brain already uses for sessions and
experiments.

**Cost of C:** two start-shaped tools remain where one could exist — a
small, real ongoing cost (two docstrings for one contract's two halves).

### Why not a flag (the rejected third option)

Collapsing to `consult(…, wait=false)` deletes the most, but turns the
authorization model into a boolean. For an LLM caller a forgotten or
hallucinated `wait=false` silently flips "human approves each trade" into
"budget is the only gate", and the utterances that trigger it ("have X do
this") carry no marker. This is the general rule the whole design obeys:

> A flag may carry a semantic difference only if setting it is
> authorization-**tightening** (fails safe). Authorization-**loosening**
> differences must be verb-shaped (fail legible).

`dry_run: true` *tightens* (worst case of a wrong add is a harmless no-op;
the dangerous direction — forgetting it — is guarded by hard lexical
markers: "test", "try", "simulate", "dry run"). `wait: false` *loosens*.
That asymmetry is why experiment stays a flag and delegate stays a verb.

### Confusion pairs: which mistakes fail safe

| Confusion | Direction that hurts | What protects it |
|---|---|---|
| session ↔ experiment | user wanted a rehearsal, brain forgets `dry_run` | rehearsal markers hard-bound to `dry_run: true` |
| consult ↔ delegate | user expected to approve, brain detaches | the **verb name** (A and C) — visible in transcript |
| session ↔ delegate | errand pollutes the track record, or mandate-trading escapes journal-seeded risk | the **whose-goal** grammar test ("run \<agent\>" vs "have \<agent\> do \<task\>") |

## The routing tree (drop-in for CONDOR.md + MCP instructions)

```
Picking the execution verb — three questions, in order:

1. WHOSE GOAL? If the user wants the agent's own strategy to run
   ("run/start <agent>", "let it trade", "kick off the trender")
   → run_agent. It executes the agent's default_config (its
   strategy/mandate). Rehearsal markers anywhere — "test", "try
   it out", "dry run", "simulate", "without real money" — MUST set
   dry_run: true. Steering words ("focused on SOL", "10 ticks") are
   trading_context / max_ticks on the same session, not a new task.

2. Otherwise the user specified a one-off TASK (the agent's
   expertise applied to a goal, NOT its default_config loop). ASKING
   OR STAYING? A question, or work they'll supervise and approve
   → consult. It blocks; every trade goes to the user. DEFAULT for
   "have <agent> do X" — presence is assumed.

3. Only with an EXPLICIT detachment signal — "in the background",
   "ping me when done", "while I'm out", "don't wait" → delegate;
   for a trading agent you MUST name the budget (risk_limits).
   Never delegate to skip an approval the user is present to give.
   No budget nameable and no baseline → stay with consult.

Boundary test when unsure between run and task: does the work
finish by itself? "Until done" is a task → consult/delegate.
"Until stopped / N ticks" is the mandate → run_agent.

Status/stop/history of ANY run — session, experiment, consult, or
delegation — go through get_run / control_run / list_runs.
```

That last line is one fewer decision than today (where delegations need
their own status/stop verbs) — a direct dividend of Design C.

## Recommendation

- **Question 1 → C.** Keep two verbs (authorization stays in the name),
  delete the parallel lifecycle. The verb space then has a clean shape:
  **three verbs for three authorization models** (journal-seeded mandate /
  human-gated task / budget-gated task), **one tightening flag**
  (`dry_run`), and **one shared track-side surface** (the run tools) for
  everything.
- **Question 2 → keep one file; don't split `strategy.md`.** The config
  doesn't cleave along run-vs-task — operating context, guardrails, and
  tactic defaults are shared; only loop cadence is run-only. The mandate/task
  split is carried by the **gate** and the **run lifecycle**, not by config
  layout. Two cleanups make the existing boundary honest: fix the venue leak
  (resolve accounts from `denomination`/`account`, never
  `default_config.venue`), and name the run-only cadence sub-block. Defer a
  real `strategies/<slug>.md` until an agent needs multiple strategies or
  config versioning — the only need it genuinely serves.

Together these make the mandate/task distinction the user asked to sharpen
**explicit without new structure**: the verb name carries it at call time
(gate), the run lifecycle carries it (loop + journal + track record), and
the run tools carry it on the track side.

Sequencing (each step independently shippable):

1. **C.1** — delete `delegate.list`/`get`/`stop` (control + the MCP tool's
   `action`/`task_id` params); return `run_id` from start; point
   `next_steps` at `get_run`/`control_run`. Update the CONDOR.md routing
   line + MCP instructions with the tree above.
2. **C.2** — collapse `DelegateTask` to a notify-on-done hook; `delegate.py`
   lands well under half its 317 lines.
3. **Q2.1** — fix the venue leak: account/venue resolution reads the agent's
   `denomination`/`account` on every path, never `default_config.venue`.
4. **Q2.2** — name the run-only cadence sub-block (`frequency_sec`/
   `execution_mode`/`max_ticks`) so it's visible which keys a task ignores.
5. **strategy file** — deferred; open only on a concrete multi-strategy or
   config-versioning need.

One explicit non-goal: do **not** fold `consult` into `run_agent`. A consult
is attended and human-gated, running one invocation; a session is the
agent's standing capital mandate — the cadence loop under a journal-seeded
gate, writing the track record. Same brain, same guards, same account; what
differs is the gate, the loop, and the books. Different questions, different
answers — that boundary earns its keep.

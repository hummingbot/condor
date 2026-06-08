# Architecture Review — Condor "Agent Fleet" Redesign

*Produced by the **Architect Reviewer** agent (`assistants/architect_reviewer.md`).*
*Date: 2026-06-08.*
*Proposal under review: `docs/agent-fleet-redesign.md`. Reviewed against the live code in
`condor/trading_agent/`, `condor/acp/`, `mcp_servers/condor/tools/`, `condor/reports.py`, and
`main.py`, not the proposal's self-description.*

---

## Verdict

**Ship-with-changes — but stage it differently than proposed.** The reframe (agent = typed
role; keep the deterministic triad; promote reports to an addressed artifact bus; one Main
agent) is well-precedented and mostly faithful to OpenClaw / Pentagon / Hermes. Phase 1
(capability profiles) is a clean, low-risk refactor and should ship roughly as written. But
the proposal **under-specifies the three hardest problems in any multi-agent harness — state
durability across restart, A2A cycle/feedback safety, and the Main-agent single-point-of-
failure — and it sequences all three to the *end* of the plan (Phases 3–4), which is exactly
backwards.** Two findings are blockers because they are foundational and get harder to retrofit
the later they land. None of the blockers are conceptual dead-ends; they are missing
specifications.

---

## Strengths (preserve these)

- **[STRENGTH] One tick loop for every kind (§5).** Keeping `TickEngine._tick` as the single
  loop and making `kind` only resolve a *profile* is the right call and matches the KB's
  "single tick loop; kinds differ only by profile" strong-answer. The loop in `engine.py`
  (lines 218–410) is genuinely role-agnostic today — it captures text + tool calls and writes
  a snapshot; nothing in it is trading-specific except the prompt and the providers. The
  reframe is mostly *subtraction* of hardcoded assumptions, not new machinery.

- **[STRENGTH] Exposure-vs-registration done right (§2.2, §3).** The capability profile
  approach — register the full MCP catalog, expose a per-capability subset via
  `build_mcp_servers_for_agent`, with the `permission_callback` as a backstop — is exactly
  Hermes's separation and is *defence in depth*: a Collector cannot call `manage_executors`
  because the tool is not in its server set **and** the risk callback refuses it. This is real
  least-privilege, not prompt-deep. Faithful adoption.

- **[STRENGTH] Topic addressing over producer addressing (§4.2).** `bus.latest(topic)` keyed on
  a stable topic name (not producer id) means you can swap which agent produces
  `arb.opportunities` without rewiring consumers. This is the Pentagon/typed-channel answer to
  the KB's "addressing" lens and the correct choice.

- **[STRENGTH] Compression-as-lineage is already satisfied — and the proposal preserves it.**
  This was a flagged risk; the code clears it. `write_summary` (`journal.py:637`) overwrites
  *only* the rolling `## Summary` section, but `save_full_snapshot` (`journal.py:476`) writes
  the complete prompt/response/tool-calls to `snapshots/snapshot_N.md` and is never rewritten.
  History is referenced, not destroyed — Hermes's lineage model. Keep both, and (see findings)
  apply the same discipline to the bus.

- **[STRENGTH] Determinism boundary is articulated correctly (§6, Pipeline B).** "The manager
  agent doesn't place orders — it manages V2 controllers that do" is the cleanest statement of
  the LLM-judgment / code-muscle split in the whole document, and it is *already* how executors
  work (`controller_id == agent_id` isolation). Managers-as-a-distinct-kind is justified.

- **[STRENGTH] Honest "where we are today" (§1).** The limitations table is accurate against the
  code; the proposal does not oversell the current state.

---

## Findings

### Blockers

- **[BLOCKER] State & restart durability is unaddressed; `_engines` is in-memory and nothing
  rehydrates it — and the redesign makes a restart *worse*, not better. — §2.4, §10
  ("Bus durability"), `engine.py:39`, `main.py:582`.**
  Today `_engines: dict[str, TickEngine]` (`engine.py:39`) is a module-level dict. On shutdown
  `post_shutdown` (`main.py:582–588`) stops every engine; **there is no startup hook that
  restores running agents** (I checked `main.py`'s startup path — it restores scheduled routine
  jobs at line 451–453 but never re-spawns `TickEngine`s). So today a process restart silently
  stops the whole fleet, and a human notices and restarts agents by hand. The redesign removes
  the human from that loop: the **Main agent** is now the launcher (§2.4), its desired-fleet
  *intent* lives only in its `agent.md` prose, and the live wiring (who consumes what, which
  triggers are armed) lives only in the in-memory engines and an as-yet-unspecified bus. After
  a crash:
  - all sub-agent engines are gone (in-memory),
  - the bus's `subscribe()` registrations (`§4.2`) are gone unless durable,
  - `on_artifact` push triggers are gone,
  - and *the only thing that rebuilds them is the next Main tick* — which itself must be
    re-launched by Condor and must correctly re-derive the entire fleet from prose.
  This is the difference between LangGraph (durable checkpointed state, resumable mid-pipeline)
  and a system that loses its topology on every deploy. **Recommendation:** before A2A ships,
  make the fleet's *desired and actual* state durable and reconcilable: (a) persist per-agent
  runtime config + status to disk (the `sessions/session_N/config.yml` mechanism already
  exists — extend it with `running: bool`, `trigger`, `produces`, `consumes`); (b) add a
  startup reconciler that re-spawns engines marked running and re-arms triggers/subscriptions
  from disk; (c) make the bus's subscription table and the artifact log file/SQLite-backed
  (the proposal says "start file/SQLite" for artifacts in §4.2 but never says the *subscription
  table* is durable — it must be). Treat "survives `kill -9` and a deploy" as an explicit
  acceptance test for Phase 2, not a Phase-3+ afterthought.

- **[BLOCKER] A2A cycle/feedback safety is named as an open question but not designed; the
  reconciling Main agent makes loops *probable*, not hypothetical. — §4.3 (push), §2.4
  (reconciliation), §10 ("Cycle detection").**
  The proposal explicitly defers cycle detection to §10 as a risk to "revisit," yet §2.4
  describes a Main agent that runs a *reconciliation loop* every 300s, and §4.3 describes
  `on_artifact` push triggers that wake consumers, which publish, which wake further consumers.
  Three concrete failure modes the design currently permits:
  1. **Direct A→B→A.** Portfolio Watcher (§6 Pipeline A) consumes `desk.recommendations` and
     also watches positions; if it ever publishes an artifact a recommender consumes, you have
     a 2-cycle with no debounce. The bus's `publish` (`§4.2`) has no fan-out cap, no per-edge
     debounce, no idempotency key.
  2. **Reconciliation thrash.** If a sub-agent erroring causes Main to stop it, and a stale
     consumed artifact makes Main's *next* tick think it's "missing" and re-create it, Main
     oscillates create/stop on a 5-minute period — burning a full agent spawn (an ACP
     subprocess + MCP servers, see `ACPClient.start`) each cycle. There is no idempotency on
     "create_strategy / start_agent" and no record that "I already tried this and it failed."
  3. **Self-wake.** Nothing stops an agent that both `produces` and `consumes` overlapping
     topics from waking itself.
  **Recommendation:** make these first-class in `bus.publish`, not a later patch:
  per-(topic,producer) **debounce window**, a **max fan-out** per publish, a monotonic
  **artifact sequence + idempotency key** so a consumer can skip an artifact it already
  processed, a **cycle guard** (carry a bounded `causation_chain` of agent_ids on each
  artifact; refuse to wake an agent already in the chain), and a **per-tick global budget**
  (max ticks/min across the fleet) with a circuit-breaker that pages the human. For Main's
  reconciliation specifically: require it to record intended actions to durable state and make
  create/start **idempotent** (no-op if the agent already exists/running) — this is the
  LangGraph/Kubernetes-controller discipline the proposal gestures at ("this is a
  reconciliation loop") but doesn't enforce. Ship this *with* push (Phase 3), gated so push
  cannot be enabled without the guards.

### Major

- **[MAJOR] Main agent is a single point of failure for *both* launching and user contact, with
  no safe default. — §2.4.**
  The proposal makes Main "both supervisor and the user's single point of contact," and
  explicitly says the human "talks to / hears from the main agent, not twenty sub-agents." The
  KB's orchestrator-failure lens asks: "if the Main agent is the single launcher + single user
  contact, what happens when *it* is down or wrong? Where's the safe default?" The proposal has
  no answer. If Main is wedged (ACP timeout — the loop has a 300s `asyncio.timeout` at
  `engine.py:305`, and a hard 31-min ceiling in `acp/client.py:303`), hallucinating, or simply
  stopped, then: no one creates/heals sub-agents, **and** the user gets no `fleet_brief` and so
  doesn't *know* the fleet is unsupervised. Sub-agents keep trading with real capital under a
  dead supervisor. Contrast Pentagon, where agents "ping the human only when needed" directly —
  the human contact is not funneled through a single fallible orchestrator.
  **Recommendation:** (a) keep a **direct, code-level (non-LLM) escalation path** to the human
  that does not depend on Main — a watchdog that detects "Main has not completed a tick in N
  intervals" or "Main last_error set" and notifies the admin via the existing `_notify`/Telegram
  path (`engine.py:513`); (b) define the **safe default when Main is down**: do sub-agents
  continue, pause, or stop? The proposal must pick one. Given real capital, recommend
  sub-agents *continue under their existing risk limits* (they're isolated by `controller_id`)
  but **no new agents are created and no directives flow** — i.e. the fleet freezes its
  topology and the watchdog escalates. (c) Strongly consider keeping Overseer **separate** from
  Main precisely so the governance/kill-switch path survives a Main failure (see next finding).

- **[MAJOR] Collapsing Main and Overseer removes the independent safety authority. — §2.4
  ("Relationship to Overseer").**
  The proposal offers "a small deployment can grant the main agent both `agent_manager` and
  `risk_manager` and run a single root." That is convenient but it means *the agent that builds
  the fleet is also the agent that polices it* — there is no independent veto if Main's
  judgment is the thing that's wrong. This is the classic separation-of-duties violation; the
  governance lens wants a Risk/Overseer veto that is *not* the same actor as the builder.
  **Recommendation:** the **kill-switch and fleet risk limits must be enforced in code**
  (extend `RiskEngine` to a fleet-level aggregate over `controller_id`s), not as an LLM
  capability that Main may or may not exercise. Let Overseer be an *optional reasoning layer on
  top* of a deterministic fleet-risk backstop, and keep the deterministic backstop alive even
  when Main has both capabilities. Do not let "small fleet, one root" mean "no independent
  safety authority."

- **[MAJOR] `call_agent` synchronous request/response via `run_once` will deadlock and blow the
  tick budget. — §4.3 mode 3, §4.5.**
  "Implemented as a short-lived `run_once` of the callee … returning its artifact" and "blocks
  on the answer." Concretely: a Trader tick (already inside a 300s `asyncio.timeout`,
  `engine.py:305`) calls `call_agent(risk_manager, …)`, which spawns a *new* ACP subprocess for
  the callee, runs a full tick (its own provider pre-compute + LLM call, easily tens of
  seconds), and the caller blocks. Nest two of these and you exceed the caller's timeout; have
  A call B while B calls A and you deadlock until both time out. There is no depth limit, no
  per-call timeout distinct from the tick timeout, and no cycle guard (same root cause as the
  push-cycle blocker). Swarm/Agents-SDK handoffs and the A2A protocol's Task model both treat
  this as async with explicit state; Condor is proposing synchronous blocking spawns.
  **Recommendation:** either (a) make `call_agent` **async** — enqueue a task on the callee and
  return a pending artifact handle the caller reads next tick (this fits the bus model and
  avoids nested subprocesses), or (b) if synchronous is required, enforce a **shallow depth cap
  (≤1)**, a **dedicated short timeout** well under the caller's, and a **cycle guard via the
  causation chain**. For the specific "can I add exposure?" use case, prefer a deterministic
  fleet-risk *check* (code) over a synchronous LLM round-trip.

- **[MAJOR] "Push" A2A is specified as a wake/enqueue, but the engine has no tick *queue* — and
  the loop sleeps `frequency_sec` between ticks. — §4.3 mode 2, §5, `engine.py:177–216`.**
  The proposal says push "enqueues a tick" and "reuses `inject_directive`-style queuing." But
  `inject_directive` (`engine.py:152`) only appends a string to `_pending_directives`; it does
  **not** wake the loop. The loop is a fixed `while self._running: tick; await asyncio.sleep(freq)`
  (`engine.py:180–214`) with no event to interrupt the sleep. So an `on_artifact` push today
  would, at best, be *picked up on the next scheduled tick* — i.e. it's a disguised poll bounded
  by `frequency_sec`, exactly the anti-pattern the KB's "event-driven, not polling" lens (from
  Pentagon) warns against. For a 5-minute-interval consumer this adds up to 5 minutes of latency
  to a "push."
  **Recommendation:** add a real wake primitive: replace the bare `asyncio.sleep(freq)` with an
  `asyncio.wait_for(self._wake_event.wait(), timeout=freq)` so a bus publish can `set()` the
  event and trigger an immediate tick; or give each engine an `asyncio.Queue` of wake-reasons
  the loop drains. This is small but load-bearing — without it, "push triggers" in §4.3 and the
  whole Pipeline-B "arb scanner publishes → arb manager wakes and acts" claim (§6) are not
  actually event-driven.

- **[MAJOR] Schema governance and validation are hand-waved, but the design leans on typed
  artifacts. — §4.1, §4.2 ("Schema-tagging lets consumers validate"), §10.**
  The whole value of `schema: arb_opportunity_v1` is that a consumer can trust the shape of
  `data`. But §10 reduces schema ownership to "propose a small `schemas/` registry" with no
  decision on *who validates when*, what happens on a schema-mismatch (drop? quarantine? flag in
  prompt?), or how versions evolve (`arb_opportunity_v1` → `_v2`) without breaking live
  consumers. With LLM producers, malformed `data` is not an edge case — it's the *default
  failure mode*. An Analyst that blindly trusts `data` from a Collector that hallucinated a
  field will act on garbage.
  **Recommendation:** make schema validation a **bus responsibility on publish** (reject/quarantine
  invalid artifacts; never let them reach a consumer's prompt), define a versioning rule
  (consumers declare the major version they accept; bus serves the latest compatible), and decide
  the consumer-side contract: the `summary` (LLM-readable) is best-effort, but `data`
  (machine-consumed) must validate or the artifact is marked degraded and the consumer is told.
  This should land in Phase 2 with the bus, not be deferred.

### Minor

- **[MINOR] Cost model is acknowledged but not bounded. — §10 ("Cost").**
  "More agents × more ticks × LLM calls" is correctly flagged and the "cheap models for
  collectors" mitigation is sound (the per-session `agent_key` override already supports it,
  `engine.py:448`). But there's no *budget ceiling*. A reconciling Main that spawns agents plus
  push cycles can multiply tick volume without an upper bound. **Recommendation:** a fleet-wide
  ticks/min and tokens/hour budget with the circuit-breaker from the cycle-safety blocker;
  surface per-tick cost in snapshots (the snapshot already records duration — add
  model/token estimate).

- **[MINOR] `kind: trader` back-compat default is asserted but the read path isn't audited. —
  §2.3, §10 ("Back-compat surface").**
  The claim "absent `kind`/`capabilities`, an agent behaves exactly as today" is only true if
  every consumer of strategy frontmatter tolerates the new fields and the missing-field
  defaults. `_load_strategy_from_file` (`strategy.py:73`) currently ignores unknown frontmatter
  keys (good) but also does **not** parse `kind`/`capabilities`/`produces`/`consumes` — so
  `Strategy` has no place to hold them yet. §10 already flags that `web/routes/agents.py` and
  the Telegram flow assume executors/PnL (e.g. performance compute) for every agent; a
  read-only Collector has no PnL. **Recommendation:** in Phase 1, add the fields to `Strategy`
  with trader-defaults, and audit every place that computes PnL/exposure/performance to no-op
  cleanly for non-trading kinds (the `get_info` dict at `engine.py:521` is PnL-centric and the
  web layer mirrors it).

- **[MINOR] Artifact retention vs. snapshot retention can silently drop fleet memory. — §4.1
  (`ttl_sec`), §7, `reports.py:24` (`MAX_REPORTS=100`), `journal.py:63` (`MAX_SNAPSHOTS=100`).**
  §7 promotes the artifact registry to "fleet memory," but the underlying report store caps at
  `MAX_REPORTS=100` and evicts oldest (`reports.py:_cleanup_locked`). If artifacts reuse that
  store (the proposal says they extend `ReportBuilder`), a busy fleet will evict an artifact a
  slow consumer still needs — and a `pull` consumer reading `bus.latest(topic)` after eviction
  gets nothing with no signal that history existed. **Recommendation:** keep the **artifact
  index** (envelope + summary + data) separate from and longer-lived than the heavy HTML report
  bodies; the small JSON envelope is cheap to retain. Define per-topic retention explicitly.

- **[MINOR] "Main is launched by Condor" needs a concrete bootstrap and a placement decision. —
  §2.4.**
  The proposal hedges ("exact placement is an implementation detail … reserved `_main/`
  folder"). But `StrategyStore._iter_agent_dirs` (`strategy.py:274`) already skips dirs starting
  with `_`, so a `_main/` data dir won't collide — good — yet the *root* `agent.md` sitting
  directly under `trading_agents/` (not in a slug dir) is not something `_iter_agent_dirs` or
  `get_by_slug` can load today (they expect `trading_agents/{slug}/agent.md`). This is a real
  schema change, not just an implementation detail. **Recommendation:** make Main a normal slug
  dir (`trading_agents/_main/agent.md`) rather than a bare root file, so the existing store
  loads it with zero special-casing; "well-known slug" is enough to be the root.

### Nits

- **[NIT] Naming.** §10 already lands on "Condor Fleet" / "fleet view" — good; just commit to
  it and drop "Pentagon" from user-facing copy (keep it as the design citation it is).
- **[NIT] §4.6 A2A-protocol mapping is aspirational and fine as such**, but flag clearly that
  the in-process bus is *not* wire-A2A and the "later adapter" claim is unverified until the
  envelope is frozen against the public Agent Card / Task / Artifact shapes. Keeping the
  vocabulary aligned is cheap insurance; don't let it imply day-one interop.
- **[NIT] `inject_directive` clears directives after one tick (`engine.py:294`)** — fine, but
  the `fleet.directive`→`inject_directive` mapping (§4.4) means a control-plane directive
  evaporates after one consume. For "reduce risk, news in 10m," one-shot is probably wrong;
  decide whether directives have their own TTL.

---

## Comparison to prior art

| Decision (proposal) | Closest prior art | Verdict |
|---|---|---|
| Typed `kind` resolves a profile; one tick loop | CrewAI roles; Claude SDK subagents | **Faithful.** Kinds = roles; loop reuse is correct. |
| Register all MCP tools, expose per-capability subset + permission backstop | **Hermes** exposure≠registration | **Faithful, a real strength.** Defence in depth. |
| Topic-addressed artifact bus | **Pentagon** typed channels; Google A2A Artifact | **Faithful** on addressing; **under-specified** on durability/validation. |
| Push via `on_artifact` trigger | **Pentagon** event-driven (no polling) | **Diverges in practice** — no wake primitive in the loop today; currently a disguised poll. Fix the loop. |
| `call_agent` = sync `run_once` of callee | **Swarm** handoffs / **A2A** Task (both async) | **Unjustified divergence.** Sync blocking spawn risks deadlock; prefer async task + handle. |
| Main agent = single launcher + single user contact | **OpenClaw** ACP-harness (main spawns sub-agents) | **Partially faithful** (spawn pattern is OpenClaw) but **no failure story**; OpenClaw/Pentagon don't funnel all human contact through one fallible LLM. |
| Main + Overseer optionally merged | Pentagon granular access; separation-of-duties | **Risky divergence.** Builder ≠ policer; keep a code-level fleet-risk backstop independent of Main. |
| Reconciliation loop in Main's prose | **LangGraph** durable graph / K8s controller | **Right instinct, missing the mechanics** — needs durable desired-state + idempotency, not prose. |
| Compression = lineage (journal summary + snapshots) | **Hermes** lineage | **Already faithful** in code; extend the same rule to the bus. |
| State across restart | **LangGraph** checkpoints | **Missing.** `_engines` in-memory; no startup restore. Biggest gap. |

---

## Open questions the author must answer before build

1. **Restart:** What is durable, and who rehydrates it on boot? Define the on-disk
   representation of (a) which agents should be running, (b) their triggers, (c) bus
   subscriptions, and (d) the reconciler that restores them. (Blocks Phase 2.)
2. **Cycle/budget:** What are the concrete debounce window, max fan-out, causation-chain depth,
   and fleet ticks/min ceiling? What trips the circuit breaker and who gets paged? (Blocks
   Phase 3.)
3. **Main down:** What is the *coded* (non-LLM) watchdog, and what is the safe default for
   sub-agents when Main is dead — continue / pause / stop? (Blocks Phase 4, arguably earlier.)
4. **Independent kill-switch:** Is fleet-level risk enforced in `RiskEngine` (code) independently
   of whether an Overseer/Main LLM chooses to act? (Should be a Phase-1/2 invariant.)
5. **Schema lifecycle:** Who owns schemas, where does validation run, what happens on
   mismatch, and how do versions evolve without breaking live consumers? (Blocks Phase 2.)
6. **`call_agent` semantics:** Async handle or sync-with-depth-cap? Decide before any control
   plane work.
7. **Artifact retention:** Per-topic TTL and the separation of cheap envelopes from heavy HTML
   bodies — so "fleet memory" isn't silently evicted at 100 reports.
8. **Non-trader read paths:** Enumerate every PnL/performance/exposure computation in
   `engine.get_info`, `web/routes/agents.py`, and the Telegram flow that assumes a trader, and
   confirm each no-ops for read-only kinds.

---

## Suggested sequencing changes

The proposal's Phase 1 is good. The problem is that the **three foundational safety properties
(durability, cycle safety, orchestrator-failure) are all pushed to Phases 3–4**, after A2A
and the Main agent already depend on them. Resequence so safety lands *with or before* the
capability that needs it:

- **Phase 1 — Capability profiles (keep as-is).** Plus: parse `kind`/`capabilities`/`produces`/
  `consumes` into `Strategy` with trader-defaults, and audit non-trader read paths (the §10
  back-compat item — do it now, not at the end). Add the **deterministic fleet-risk backstop**
  scaffold here (it's just a `RiskEngine` that aggregates across `controller_id`s), even before
  there's an Overseer to drive it.
- **Phase 2 — Artifacts + pull A2A + durability + schema validation (merge the durability work
  in).** Ship the bus *file/SQLite-backed including the subscription table*, schema validation
  on publish, separated envelope/body retention, **and** the startup reconciler that re-spawns
  running agents from disk. Acceptance test: `kill -9` the process, restart, fleet comes back.
  Pull-only means no cycles yet, so this is the safe place to prove durability.
- **Phase 3 — Triggers + push A2A *with* cycle/budget guards (do not split them).** Add the
  loop **wake primitive** (so push is truly event-driven), and ship debounce + max fan-out +
  causation-chain cycle guard + fleet tick budget + circuit breaker *in the same phase as
  push*. Push must be ungate-able without the guards.
- **Phase 4 — Control plane + Main agent + watchdog (add the failure story).** Make `call_agent`
  async (or depth-capped). Ship the **code-level Main watchdog** and the **safe-default-when-
  Main-down** policy alongside the Main agent itself — not after. Keep Overseer's kill-switch
  enforced in code, independent of Main.
- **Phase 5 — Pentagon frontend (keep as-is).** Add a *"Main health / last tick"* indicator and
  a *"stale/orphaned edge"* surface to the topology — the UI is the natural place to make the
  watchdog and cycle-guard state legible to the human.

Net: nothing in the proposal is wrong-headed; the staging just needs to stop treating
durability, cycle safety, and orchestrator failure as polish. They are the load-bearing walls.

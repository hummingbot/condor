# Condor roadmap v2 — MCP-first, strategy-engine-centered

Supersedes [`roadmap.md`](./roadmap.md) (kept for history, not deleted).
Companion to [`docs/architecture/agent-framework.md`](../architecture/agent-framework.md),
[`docs/strategy/business-strategy.md`](./business-strategy.md),
[`docs/strategy/fork-vs-build.md`](./fork-vs-build.md), and
[`docs/strategy/mcp-first-value-add.md`](./mcp-first-value-add.md). For the
concrete technical design of the two systems Phase 1/2 below build — the
Strategy Engine and Shared Intelligence — see
[`docs/architecture/strategy-engine-and-shared-intelligence.md`](../architecture/strategy-engine-and-shared-intelligence.md).

**Why a v2, not an edit to v1**: three pieces of research landed after v1
was written — confirming any MCP-capable harness can consume Condor's tool
server directly (`mcp-first-value-add.md`), a live competitor (Tread Labs)
independently validating that the chat/tool-wrapping layer is commodity and
the unattended-execution layer is the real moat, and a concrete new
network-effect lever (business-strategy.md §13's shared learnings store).
Together they don't just add scope to v1 — they change what Condor's
engineering effort should be *centered on*. This doc restates the plan
around that center rather than patching it in as another bullet list.

**The thesis this whole plan is organized around**: Condor is a strategy
*engine* — an always-on, self-documenting, audited execution runtime for
trading strategies — with a thin, swappable tool interface on one side and
accumulated domain content and cross-tenant intelligence on the other. The
chat frontend (Telegram bot, web dashboard) is not that engine, was never
going to be the moat, and should stop being treated as the flagship
investment. Everything below is organized around building the engine and
the content, and around making the tool interface work with *any* harness
rather than only Condor's own.

**Priority order, stated explicitly**: Condor Refactor → Shared
Intelligence → hosting-as-monetization. Shape A/Swig integration and any
tokenization work (v1's Phase 3) are **removed from this roadmap for
now** — see the status notes at business-strategy.md §7/§9. They remain
documented background research, not active scope.

## 0. Architecture principle: three tiers, one of them optional

Restating `mcp-first-value-add.md`'s finding as a design principle, because
it has to be internalized before Phase 1's scope makes sense: **"MCP-first"
narrows what's optional to the chat frontend specifically — it does not
mean the backend/`TickEngine` becomes optional.** A meaningful slice of
what makes Condor useful (`consult`, `delegate`, strategy management) is
proxied by the MCP server into Condor's own backend web API
(`condor_client.py`, per that doc's §2) precisely because `TickEngine`s
"must be created in the main process so they survive beyond the MCP
subprocess lifecycle." That dependency doesn't go away because a user
picked Claude Code over Condor's own Telegram bot — it just becomes
independent of which chat surface they picked.

So the honest architecture has three tiers, not two:

1. **Tool Interface (MCP server)** — thin, always stdio (self-hosted or
   hosted alike — a hosted box co-locates its harness with the MCP server
   exactly as self-hosted does, Phase 2), harness-agnostic by construction
   already. Commodity, per `mcp-first-value-add.md` §4 — don't over-invest
   here beyond correctness (Tread Labs' `isError` gotcha) and the
   live-catalog pattern worth adopting from that doc's §5.
2. **Strategy Engine (backend + `TickEngine` + journal/learnings/shutdown-
   policy)** — not optional, not redundant with any external harness, and
   the actual product per `mcp-first-value-add.md` §4 and business-
   strategy.md §10/§12. Must run somewhere reachable: a lightweight local
   process for self-hosted users (today's model, unchanged), or Condor's
   own managed box for hosted customers (Phase 2, below).
3. **Shared Intelligence (the cross-tenant learnings store, business-
   strategy.md §13)** — opt-in, centrally operated, and — new in this
   version of the plan — available to self-hosted installs, not just
   hosted customers (see Phase 1 below). This is a genuinely different kind
   of service from Tier 2: it holds no capital and no exchange credentials,
   so it doesn't need Tier 2's single-tenant isolation story (business-
   strategy.md §12) — one central, ordinary multi-tenant API is correct
   here.

**Frontend (optional, by design)**: whichever harness a human prefers —
Claude Code, Cursor, Hermes-agent, or Condor's own Telegram bot/web chat as
one first-class option among several, not the center of engineering
gravity. This is the layer `roadmap.md` v1 left as an open "Keep or kill
Assistants?" question; this version resolves *how much investment* it gets
(see Phase 1 below), even where the underlying schema question stays open.

**What self-hosted "MCP-first" concretely looks like**: a user runs
Condor's backend locally (already how self-hosting works today — the "main
process" behind `condor_client.py`'s `127.0.0.1:{WEB_PORT}` calls), points
their preferred harness at Condor's `.mcp.json`-shaped stdio server
(already exactly the shape `build_mcp_servers_for_session` produces, per
`mcp-first-value-add.md` §3), and gets full `consult`/`delegate`/strategy
functionality because the backend is running, chat frontend or not. What
changes under this plan isn't whether the backend runs — it's that Condor
stops assuming its own Telegram/ACP session is how that backend gets used.

## Phase 0: Foundations (July 2026)

Same core alignment/cost-research items as v1, plus two new research items
this version's Phase 2 design requires:

- **Agree on this roadmap** — same stakeholders/scope as v1 (brand surface,
  Foundation gross-margin split, business-strategy.md §4/§8).
- **Research hosting infra costs** — same methodology as v1 (per-session
  footprint, per-tick LLM cost as the dominant variable cost, the
  bundled-credit margin-risk failure mode, single-tenant-VM-per-customer
  costing per business-strategy.md §12) — **now explicitly informed by
  whether Phase 2 uses a local model** (below), since that changes the
  per-tick cost line from "metered third-party API spend" to "amortized
  compute on owned/rented hardware," a materially different and more
  predictable cost shape.
- **NEW: local-LLM hosting research for Phase 2.** This isn't a green-field
  question — Condor's own session layer already has the hook.
  `handlers/agents/session.py` already branches on `is_pydantic_ai_model`
  and constructs a `PydanticAIClient` for `agent_key` values like
  `"ollama:model"` / `"lmstudio:model"` (see the `agent_key` docstring at
  `session.py:38` and the `base_url`/`LMSTUDIO_BASE_URL` resolution at
  `session.py:170-181`) — i.e., Condor can already talk to a locally served
  model, today, for interactive sessions. `strategy.md`'s own frontmatter
  already carries an `agent_key` field (architecture doc §4) in the same
  format, which is the natural, already-existing hook for pointing a
  `TickEngine`'s recurring ticks at a local model instead of a hosted
  frontier API — this needs verifying against `engine.py`'s actual client
  construction, but the frontmatter shape strongly suggests this is the
  intended integration point, not new plumbing. Phase 0's research task:
  pick a candidate serving stack (vLLM/SGLang/Ollama-compatible), a
  candidate open-weight model class, and benchmark tick-loop-realistic
  prompts (strategy playbook + recent journal context, per architecture
  doc §4) for quality/latency/cost against the current frontier-API-only
  baseline.
- **NEW: which harness runs on the hosted box, and how Telegram reaches
  it.** Correcting an assumption from an earlier pass of this plan: a
  hosted customer's harness is **not** remote from their strategy engine —
  it runs on the same single-tenant box, exactly like self-hosted. The
  harness (Condor's own runtime, OpenClaw, or Hermes) still talks to
  Condor's MCP server over **stdio**, unchanged from today — no network MCP
  transport is needed, self-hosted or hosted. This is purely a Tier 1
  (interactive) choice — which harness answers the customer's Telegram
  messages and calls `consult`/`delegate`/`manage_trading_agent` — and is
  independent of who schedules a running strategy's recurring ticks:
  Condor's own `TickEngine` remains the tick scheduler regardless of which
  harness is installed on the box (pluggable scheduling is deferred, see
  the Condor Refactor section below), so an OpenClaw- or Hermes-fronted box
  still runs Tier 2 exactly as a Condor-fronted one does. What Phase 0
  actually needs to decide is provisioning-time, not transport-time: which
  harness gets installed on a given customer's box (a choice made when the
  box is set up, not a client-side "point your own laptop's tool at our
  server" mechanism), and how that on-box harness's own Telegram
  integration (OpenClaw and Hermes both ship mature Telegram
  channel/platform adapters; Condor has its own) gets wired to the
  customer's chat so they can reach their instance from their phone without
  needing to run anything locally themselves. Today's process-local
  `CONDOR_CHAT_ID`/`CONDOR_USER_ID` identity model (`mcp-first-value-add.md`
  §2) stays as-is — it's still one box, one install, stdio throughout.
- **API tokens** (Phase 2 monetization, below) are a separate surface from
  this — programmatic access goes through Tier 2's own REST API
  (`condor/web/routes/agents.py`-style routes, token-authenticated), not
  through the MCP protocol over a network. No conflict with the
  stdio-only decision above.
- **Isolation architecture** — unchanged from v1: single-tenant
  VM/container per customer for Tier 2 (business-strategy.md §12), *not*
  for Tier 3 (shared intelligence, ordinary multi-tenant, see Phase 1).

## Phase 1: 3Q–4Q 2026 — Condor Refactor, then Shared Intelligence

**Priority order for this phase, stated explicitly**: the Condor Refactor
(MCP-first hardening + the enhancement/infrastructure work below) comes
first, Shared Intelligence builds on top of it, and hosting-as-monetization
is Phase 2 — not a parallel track. Dogfooding runs throughout as the
substrate both the refactor's validation and Shared Intelligence's content
depend on.

**Scope note**: Shape A (Swig-secured custody, business-strategy.md §9b)
and any tokenization work are **excluded from this roadmap for now** — see
the status note at business-strategy.md §7/§9. Dogfooding below validates
the Condor Refactor and feeds Shared Intelligence using whatever custody
model Botcamp Solutions' mandates already use today; it does not depend on
Swig landing first.

### Dogfooding (unchanged from v1, still the top priority)

- **Build agents for USDM, QA bots** — same scope as v1: formalize
  `routines/usdm_*.py` into a real `agents/usdm_expert/` domain agent with
  its own strategy, and stand up internal QA bots exercising Condor's own
  tools/routines/strategies continuously. Stays the top-weighted item in
  this phase, not because it changed, but because the accumulated
  per-strategy track record (business-strategy.md §10, `mcp-first-value-
  add.md` §4) and the new shared-intelligence corpus below both depend on
  real strategies actually running — every month this stays an informal
  script instead of a live strategy is accumulation neither asset gets
  back later.

### Condor Refactor (first)

Everything in this subsection is one umbrella: the MCP-first hardening,
enhancements, and infrastructure work that make Condor's own engine and
tool surface solid before Shared Intelligence builds on top of it and
hosting monetizes both.

#### MCP-first hardening (elevated from v1's "updated focus" note into real Phase 1 scope)

- **Publish the harness-agnostic MCP integration guide.** Near-zero cost —
  the config shape already exists internally
  (`build_mcp_servers_for_session`/`build_mcp_servers_for_agent`,
  `mcp-first-value-add.md` §3) — document it externally so Claude Code,
  Cursor, OpenClaw, Hermes, or any other MCP-capable harness can point at
  Condor's tools directly (as a Tier 1 interactive client — unrelated to,
  and available well ahead of, the deferred pluggable-scheduling work
  below), with the Tier 1/Tier 2 boundary (§0 above) spelled out clearly so
  users aren't surprised by an `APIError` from a tool that needs the
  backend running.
- **Audit the MCP tool layer for the Tread Labs-class correctness bug** —
  check every tool in `mcp_servers/condor/tools/*.py` and
  `mcp_servers/hummingbot_api` for in-band `{"ok": false}`-style failures
  that don't get surfaced as MCP `isError`, so a failed order or a failed
  tool call never silently looks like a success to the calling LLM.
- **Prototype the live-tool-catalog pattern** (`mcp-first-value-add.md` §5)
  if tool-schema iteration speed becomes a bottleneck — not urgent, but
  worth a spike given how directly it addresses client/server version skew.
- **Pluggable scheduling (OpenClaw/Hermes as tick schedulers) — deferred,
  not in this phase.** Designed in full (`docs/architecture/strategy-
  engine-and-shared-intelligence.md` §1.5–1.6: the portable tick contract,
  new lock/risk-check MCP tools, a global `strategy_tick` skill, and the
  multi-strategy registry/stop-all layer in §1.4/§1.6 Step 6), but **not
  scheduled for this phase** — Condor's own `TickEngine` loop stays the one
  and only scheduler for running strategies for now, unchanged from today.
  Revisit once a concrete need (e.g. a hosted customer whose box already
  runs OpenClaw or Hermes for other reasons, or dogfooding hitting a real
  limit of the native loop) makes it worth building, rather than building
  the adapter surface speculatively ahead of that need.
- **This is independent of harness support, which is unaffected.** OpenClaw,
  Hermes, and Claude Code (and Cursor, and any other MCP-capable harness)
  are already fully usable as Tier 1 — pointing any of them at Condor's MCP
  server to `consult`/`delegate`/`manage_trading_agent` today requires no
  new work and nothing above changes that. What's deferred is specifically
  *who fires a running strategy's recurring ticks* (Tier 2's scheduling
  loop) — not which harness a human or a hosted box uses to talk to Condor.

#### Enhancements (carried over from v1, sharpened toward the MCP-first center)

1. **Routine-first workflow** — unchanged from v1: let an agent-scoped
   session see its own local routines *plus* the global `routines/`
   library, mirroring how Condor itself already sees both.
2. **Skills shareable like routines** — unchanged from v1
   (business-strategy.md §11a): a global skills tier alongside the
   existing agent-local one. (The deferred pluggable-scheduling tick
   playbook above would also live here, as a global skill, if/when that
   work is picked up — not a dependency for this item today.)
3. **Enable agent recursion** — unchanged from v1: letting an agent
   consult/delegate to *other* agents, with the depth/cycle-limit and
   confirmation-chain design questions v1 already flagged still open and
   still needing resolution before shipping.
4. **Integrate Telegram more fully — `@Condor` in group chats** — kept, but
   explicitly scoped as *maintaining* Condor's one first-class chat option
   at its current level of capability, not a growing investment. Consistent
   with §0's principle: the frontend gets enough attention to stay good,
   not the majority of engineering time.
5. **"Keep or kill Assistants?" — the schema question stays open; the
   investment-level question does not.** v1 left this fully open. This
   version resolves the part that was actually actionable without more
   information: **redirect engineering capacity away from chat-frontend
   polish and toward the Strategy Engine and Shared Intelligence work
   below**, regardless of which side of the assistant/agent schema
   question the team eventually lands on. The schema table from v1 is
   still real and still unresolved — collapsing to agent-only vs. keeping
   the split is a legitimate architectural question with real tradeoffs
   (v1's comparison table) — but it no longer gates how much *frontend*
   investment happens either way, since §0 has already answered that part:
   not much, on purpose.
6. **Pre-defined agent templates** — unchanged from v1 (Data collection,
   Back testing, Forward testing, Strategy creation, Bot deployment,
   Routine builder, Routine scheduler) — this is Strategy Engine content
   work, squarely in the "build this" category from §0, not frontend work.

#### Infrastructure

- **Hummingbot: add a Solana connector, single Docker instance** —
  unchanged rationale from v1, but now explicitly a Phase 2 prerequisite,
  not just a nice-to-have: bundling Hummingbot execution onto one box per
  customer (Phase 2, below) is only tractable if this consolidation has
  already happened — a smaller per-customer footprint is what makes adding
  a locally served LLM to the same box affordable.
- **Biz dev: greenlight Phase 2.** Once the Condor Refactor and Shared
  Intelligence (below) are validated via dogfooding, decide timing for
  Phase 2 (hosting-as-monetization). Shape A/Swig and any tokenization
  path are out of scope for this decision — see the status note at
  business-strategy.md §7/§9; revisit them separately, later, if at all.

### Shared Intelligence (second) — built into Phase 1, available to self-hosted installs too

This is business-strategy.md §13's moat, built directly into Phase 1 —
after the Condor Refactor above, since the redaction-pipeline and
lock/risk-check plumbing it needs (`strategy-engine-and-shared-
intelligence.md` §2.5) assumes the refactor's tools already exist — with
one explicit design choice carried from the earlier draft: **self-hosted
installs get this too, not just hosted customers.** A network-effect moat
is only as strong as its participation rate — gating it behind a hosted
tier would cut it off from the majority of Condor's actual current user
base (Hummingbot's existing self-hosting community) and weaken the
flywheel from day one. Concretely:

- **A public, Condor-Foundation-operated "Condor Learnings" endpoint** that
  any install — self-hosted or hosted — can opt into, both to contribute
  and to query. This is Tier 3 from §0: an ordinary central multi-tenant
  service, unrelated to Tier 2's single-tenant execution isolation, since
  it holds no capital or exchange credentials, only redacted text/stats.
  Self-hosting stays exactly as free and independent as it is today — this
  is an *additive* opt-in call-out, not a requirement, and not a
  phone-home default.
- **`share_learnings:` frontmatter flag on `strategy.md`** (architecture
  doc §4) — off by default, per-strategy. When set, triggers a
  redaction-then-publish pipeline (not raw `learnings.md` scraping): strip
  exact position sizes, wallet/account identifiers, and anything specific
  enough to leak a named client's operating parameters (Ripple, MetaDAO,
  USDM1, business-strategy.md §1) before anything leaves the strategy's own
  store.
- **Two content tiers, with reciprocity to counter adverse selection**:
  coarse aggregate statistics broadly available to any opted-in
  participant (self-hosted or hosted); richer textual learnings reserved
  for active contributors specifically — otherwise the strategies most
  likely to opt in are the ones doing worst (looking for help), not the
  best, which would dilute the corpus's value for everyone.
- **Reputation weighting** by contributing-strategy tenure/uptime and
  whether it's backed by real capital vs. a dry run/backtest (architecture
  doc §4's experiment modes), so noisy or paper-trading-only submissions
  don't dilute the corpus. (Swig-based on-chain reputation verification was
  considered and deferred alongside Swig integration generally — see
  `strategy-engine-and-shared-intelligence.md` §2.5 step 4.)
- **A new MCP tool, `query_shared_learnings(tags, agent_type, regime,
  ...)`** — callable from any strategy's `TickEngine` reasoning or any
  chat session (Condor's own, or an external harness via Tier 1), a small,
  natural addition to the existing tool surface. Directly enables the
  cold-start use case: a new strategy on an unseen exchange/pair, or the
  next USDM-adjacent mandate, queries what other strategies (across the
  entire opted-in base, self-hosted included) have already learned instead
  of starting from zero.
- **Herding-risk mitigation designed in from the start** — keep each
  strategy's own LLM reasoning in the loop to interpret and contextually
  apply a shared learning rather than mechanically triggering on it, and
  consider staggering how fast newly published learnings propagate into
  live decision loops. Many strategies (self-hosted or hosted) reacting
  identically and simultaneously to the same shared signal is a new
  systemic-risk category this feature introduces that private, per-
  strategy journals didn't have.
- **Legal review before any paid tier** — centrally aggregating and
  (later, Phase 2) selling access to cross-user trading performance data
  can implicate investment-adviser regulation. Ship the
  opt-in/redaction/reciprocity design *before* collecting any data, not as
  a retrofit.

## Phase 2: 1Q 2027 — hosted strategy engine, not a chat product

The single biggest framing change from v1: **what's hosted is the Strategy
Engine (Tier 2), bundled with execution, a local model, and a harness, all
on one box per customer — not a chat interface Condor builds and
maintains.** The product being sold is the always-on execution/journaling/
audit-trail engine underneath; the customer reaches it via Telegram (or
another messaging channel), not by running their own local tool against a
remote endpoint.

### The bundle

One single-tenant server per customer (business-strategy.md §12 — no
shared multi-tenant fleet), containing:

- **Hummingbot + Gateway execution**, consolidated to the single Docker
  instance from Phase 1's infrastructure work.
- **A locally served LLM**, using the already-existing `ollama:`/`lmstudio:`
  `PydanticAIClient` support in `handlers/agents/session.py` as the
  integration point, extended to `TickEngine` via `strategy.md`'s own
  `agent_key` field (§0/Phase 0 above) — this is a real capability
  extension, not new architecture from scratch.
- **The Condor backend/`TickEngine`/journal/learnings/MCP stack**, reached
  over **stdio**, unchanged from self-hosted — no network MCP transport
  (Phase 0's corrected assumption, above).
- **A harness co-located on the same box** — Condor's own runtime,
  OpenClaw, or Hermes, chosen at provisioning time (Phase 0) — talking to
  the MCP stack over that same stdio connection, and exposing the
  customer's actual access point: **Telegram**, via whichever harness's own
  Telegram integration is running. The customer doesn't need their own
  laptop, their own Claude Code session, or any local tooling at all to use
  their hosted instance — the whole stack, harness included, lives on the
  box, and their phone is the only client they need.

### Why a local model, concretely

- **Cost predictability, directly resolving Phase 0's flagged risk.** v1's
  Phase 0 flagged that a bundled-compute subscription tier "injects zero
  new cash... if [third-party API] costs rise" (business-strategy.md §2's
  Venice caveat) — the dominant variable cost being per-tick LLM spend that
  scales with strategy count, not user count. A locally served model
  converts that from metered third-party API billing into amortized
  compute on owned/rented hardware — a materially more predictable cost
  shape for a tick loop firing every 120s indefinitely (architecture doc
  §4's `pmm_mister_operator` cadence).
- **Latency and no external-API uptime dependency** — the model is
  co-located with the execution engine on the same box, and a trading
  strategy's tick loop no longer depends on a third-party API's own
  availability.
- **The real tradeoff, stated plainly**: an open-weight model served
  locally is very likely weaker at hard reasoning than a frontier API
  model. This is a genuine quality cost, not a free lunch — which is why
  this isn't an all-or-nothing swap (below).

### Hybrid routing: local for routine ticks, frontier API for hard calls

Rather than fully replacing frontier models, route by task difficulty — the
same "smart friend" capability-router pattern Cognition's own multi-agent
research describes (a weaker/cheaper model handling routine work, escalating
to a stronger one for the hard subtask): the local model handles the
frequent, routine tick-loop reasoning (parameter adjustment within a
strategy's own playbook bounds, architecture doc §4), while genuinely hard
calls — initial strategy authoring, backtesting, the LLM-decision stage of
the shutdown-policy sequence (architecture doc §5), anomaly/error recovery,
and any human-facing `consult` session — escalate to a frontier API model.
This keeps the cost-predictability win where it matters most (the
high-frequency loop) without giving up quality where it matters most (rare,
high-stakes decisions).

### Explicitly not a chat/dashboard product

- The web dashboard is demoted to an **ops/monitoring surface** — status,
  logs, the journal/learnings viewer, billing — not a conversational
  product surface competing with what any harness already provides. No
  further investment in building it out as a chat interface.
- A customer's actual interaction surface is **Telegram**, reaching whatever
  harness (Condor's own, OpenClaw, or Hermes) is provisioned on their box —
  not their own local Claude Code/Cursor reaching into a remote endpoint.
  Choosing which harness runs on the box is a provisioning-time decision
  (Phase 0), not a client-side "bring your own tool" one for the hosted
  tier specifically — that framing still applies to self-hosted (anyone can
  point any local harness at their own local stdio server), it just isn't
  the hosted product's access mechanism.

### Monetization (carried over from v1, reframed around engine compute, not chat sessions)

- **Include LLM and hosting in the subscription** — same as v1, but now
  concretely cheaper/more predictable per the local-model shift above.
- **API tokens** — unchanged from v1: programmatic access to the hosted
  strategy engine, mirroring Nous Portal's API-alongside-chat model.
- **Builder codes + subscription + per-bot-instance + private box
  (enterprise)** — unchanged from v1 (Jupiter-referral-style attribution
  for third-party strategy builders, tiered subscription, usage-based
  per-instance pricing, dedicated enterprise infra) — see v1's Phase 2 for
  the full breakdown, still accurate.
- **NEW: Shared Intelligence contributor-tier access as a hosted upsell** —
  since Phase 1 already ships the corpus itself (self-hosted included),
  Phase 2's monetization angle is faster/higher-quality access (e.g.
  lower-latency queries, a larger or more curated slice of the corpus) as
  part of paid tiers, not the corpus's existence — participation stays
  open to everyone per Phase 1's design.

## Beyond Phase 2: tokenization, deferred

v1 had a "Phase 3: Maybe" here (MetaDAO ICO or Enzyme-partnership,
tokenization at the strategy layer, Swig-policy frontmatter). **Removed
from the active roadmap for now**, alongside Shape A/Swig in Phase 1 — see
the status note at business-strategy.md §7/§9 for the full analysis, kept
as background research rather than a live plan. Current priority is
Condor Refactor → Shared Intelligence → hosting-as-monetization (Phase 2)
only; tokenization is not scheduled and has no target phase until it's
deliberately revisited.

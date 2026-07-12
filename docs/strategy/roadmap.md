# Condor roadmap — phases 0 through 3

> **Superseded by [`roadmap-v2.md`](./roadmap-v2.md)** (2026-07-07), which
> reorganizes this plan around the MCP-first/strategy-engine findings in
> `fork-vs-build.md` and `mcp-first-value-add.md` and folds the shared-
> learnings moat (business-strategy.md §13) into Phase 1. Kept here for
> history — v2 is the current plan.

Companion to [`docs/architecture/agent-framework.md`](../architecture/agent-framework.md)
(what Condor *is*) and [`docs/strategy/business-strategy.md`](./business-strategy.md)
(monetization/audience/open-source analysis, especially §7's staged path and
§9–11's tokenization/agent-framework implications). This doc turns those
recommendations into a phased execution plan with concrete scope per phase.
Dates are targets, not commitments — the explicit gate at the end of Phase 1
("decide Phase 2 or 3") is the point where this plan is meant to bend around
real evidence rather than a pre-set calendar.

## Updated focus, after the fork/MCP-first research

[`fork-vs-build.md`](./fork-vs-build.md) and
[`mcp-first-value-add.md`](./mcp-first-value-add.md) — including the
Tread Labs (`taas_mcp`) competitive check in the latter's §5 — converge on
one conclusion sharp enough to change how Phase 0/1 should be weighted, not
just what they should eventually cover:

**The defensible layer is the `TickEngine`/journal/learnings runtime plus
the accumulated track record and domain content riding on top of it — not
the chat/session frontend, and not the MCP tool-wrapping glue.** A live,
funded competitor in this exact space (Tread Labs) looked at "MCP tools +
bring your own harness" and deliberately stopped there, stating outright
that it runs no server-side agent and owns no execution/audit-trail layer —
independently confirming that gap is real and is where Condor's engineering
time should concentrate.

Concretely, this reorders priority within the phases below rather than
adding new scope:

- **Weight Phase 1's dogfooding (formalizing `agents/usdm_expert/`, running
  it for real) above the enhancement items listed alongside it** — the
  accumulated per-strategy track record is a compounding asset, and every
  month it stays an informal script instead of a running strategy is
  accumulation that can't be caught up on later.
- **Treat the "Keep or kill Assistants?" decision (item 5 below) as already
  pointing in a direction**: redirect engineering capacity away from
  chat-frontend polish (confirmed redundant with any MCP-capable harness —
  Claude Code, Cursor, Hermes-agent, etc. can all already consume Condor's
  MCP server directly) and toward the runtime/content layer above. Keep the
  Telegram bot as one good, first-class option — just not the thing
  engineering time is optimizing for.
- **Publish a harness-agnostic MCP integration guide early in Phase 1** —
  near-zero cost, and it reframes the hosted-instance pitch (Phase 2) to
  "bring your own harness, we run the always-on backend" rather than
  competing on chat UX.
- **Audit the MCP tool layer for the boring-but-real correctness class of
  bug** Tread Labs had to patch (in-band `{"ok": false}` failures not
  surfaced as MCP `isError`) — quality control on the commodity interface
  layer, cheap insurance rather than a strategic investment.

## Phase 0: Foundations (July 2026)

The only phase with no engineering deliverable — it's alignment and cost
literacy before committing to anything in Phase 1.

- **Agree on this roadmap.** Specifically the open questions carried over from
  business-strategy.md §8 that this plan assumes answers to: brand surface
  for the hosted product, and the gross-margin split back to the Foundation
  (§4). Stakeholders: Feng, Cardoso, Foundation board. Nothing in Phase 1
  blocks on this except the "biz dev" framing at the end of Phase 1, but it
  should be settled in principle now rather than discovered mid-phase.
- **Research hosting infra costs.** This has to happen before §5's pricing
  tiers (Starter ~$29–49/mo, Pro ~$99–199/mo, etc.) are trusted as real
  numbers rather than placeholders. Concretely, quantify per the actual
  session mechanics in architecture doc §6:
  - Per-session compute footprint: one `claude-agent-acp` subprocess + two
    MCP server subprocesses per active chat session, plus whatever a
    `TickEngine` strategy's recurring tick costs on top (architecture doc §4
    — *each tick spins up a fresh LLM session*, not a cheap poll).
  - Per-tick LLM cost at realistic tick frequencies (`pmm_mister_operator`'s
    default is 120s — architecture doc §4's strategy.md example) — this is
    the dominant variable cost, and it scales with number of hosted
    strategies, not number of users, so it needs its own line item separate
    from "compute credits."
  - Reconcile against the bundled-credit pricing model **and its known
    failure mode**: business-strategy.md §2 flags that Venice's
    stake-for-credits design "injects zero new cash... if costs rise" —
    the same risk applies to any bundled-compute subscription tier. Model
    what happens to margin if a hosted strategy's tick frequency or model
    cost rises after a customer is locked into a flat-fee tier.
  - Hosting/VPS cost per concurrent session at the target Phase 1/2 scale
    (Botcamp Solutions' current 3 named mandates vs. an eventual external
    user base).
  - **Isolation architecture, decided now rather than defaulted into**:
    business-strategy.md §12 (researching Cognition/Devin's cloud-agent
    build) concludes every hosted tier should be a dedicated single-tenant
    VM/container per customer, not shared multi-tenant infrastructure —
    Condor's stage doesn't support replicating Cognition's year-plus,
    multi-team hypervisor-isolation investment, and doesn't need to, since
    single-tenant hosting has no cross-tenant attack surface to defend in
    the first place. Cost this out as "N small dedicated boxes," not "one
    shared elastic fleet," before Phase 2's tier pricing is finalized.

## Phase 1: 3Q–4Q 2026

This phase is Shape A (business-strategy.md §9b) plus the architecture work
that Stage 1 dogfooding is already surfacing as necessary. Nothing here is
public — per §7, this stays internal validation.

### Dogfooding

- **Build agents for USDM, QA bots.**
  - USDM: formalize the existing ad hoc `routines/usdm_*.py` scripts
    (`usdm_report.py`, `usdm_arb_execute.py`, `usdm_lp_refresh.py`,
    `usdm_orca_center.py`) into a real `agents/usdm_expert/` domain agent
    with its own `AGENT.md`, skills, and at least one `strategies/` playbook
    — this is the concrete step that turns "we're dogfooding USDM1" from an
    informal fact (business-strategy.md §1/§7) into an actual instance of
    the agent framework, and the first real test of whether the ontology in
    the architecture doc holds up under a live Botcamp Solutions mandate.
  - QA bots: internal agents whose job is exercising Condor's own MCP
    tools/routines/strategies continuously to catch regressions before a
    client mandate does — meta-dogfooding the framework with itself. Cheap
    insurance before Phase 2 puts any of this in front of external users.

### Enhancements

1. **Routine-first workflow — let agents use routines from the global
   library, not just their own local one.** Today (architecture doc §3) the
   isolation is absolute: a domain agent's session resolves *only*
   `agents/{slug}/routines/`, never the global `routines/` library that
   Condor itself can see. That's a real, concrete friction point right now:
   the repo-root `routines/` folder already has `usdm_report.py` and
   siblings that a new `usdm_expert` agent (above) arguably *should* be able
   to call directly instead of duplicating into its own local dir. The fix
   is a scoped loosening of `routines/base.py`'s resolution rule: an
   agent-scoped session sees its own local routines **plus** the global
   library (mirroring how Condor already sees global + a per-agent
   overview), not full symmetric sharing between agents. This is a narrower,
   more surgical change than §11a's skills proposal below — routines
   already have the two-tier shape, this is about which side of it an agent
   can read from.

2. **Skills shareable like routines.** This is business-strategy.md §11a
   directly: add a global skills tier (generic, tool-agnostic playbooks —
   "how to detect regime," "how to write a report") alongside the existing
   agent-local tier (specializations tied to a specific agent's own tool
   allowlist, e.g. `pmm_mister_deploy`'s dependence on `manage_controllers`).
   No-regret change per that analysis — do it regardless of what Phase 2/3
   ends up being.

3. **Enable agent recursion.** Today only Condor gets an `[AGENTS]` index
   and can `consult`/`delegate`; an agent's own injected context
   (`build_agent_context`, architecture doc §2) has no `[AGENTS]` section at
   all — it's a strict two-level hierarchy (Condor → domain agent), not a
   graph. "Recursion" means letting an agent itself consult/delegate to
   *other* agents (e.g. a strategy-creation agent calling out to the
   backtest agent below). Real design questions to resolve before shipping,
   not after: a depth/cycle limit (each consult/delegate spins up a whole
   new subprocess + LLM session per architecture doc §6 — unbounded
   recursion is a real cost and hang risk, not just a correctness one);
   whether a sub-agent's mutating tool calls still route through the same
   human-confirmation chain as a top-level consult, or auto-approve since
   the parent call may already be unattended (delegate-within-delegate);
   and whether `when_to_consult` filtering still gates which agents another
   agent can reach, or whether agents get a curated subset rather than the
   full `[AGENTS]` index Condor sees.

4. **Integrate Telegram more fully — `@Condor` in group chats.** Today's
   session model (architecture doc §6) is built around one subprocess per
   chat; extending this to respond to mentions inside a shared group thread
   (not just 1:1 DMs) is what makes the Team tier's "shared workspace"
   concept (§5) real on Telegram specifically — the natural fit is a
   client's own ops channel (e.g. USDM1/M1X's or MetaDAO's internal group)
   consulting Condor collaboratively instead of routing through one
   person's DM.

5. **Keep or kill Assistants? — the highest-impact decision in this phase,
   deliberately left open here rather than resolved.** business-strategy.md
   §11c argued for keeping `assistants/condor/` as a single opinionated
   reference coordinator, with white-labeling happening one layer down at
   the agent/strategy level (mirroring Enzyme: nobody white-labels Enzyme
   itself, they build on top of it). This roadmap item proposes something
   more radical: collapse the assistant/agent schema split entirely
   (architecture doc §1's two-column table) so **agent is the only
   composable unit**, and Condor is simply the default/root agent instance
   rather than a structurally distinct thing.

   | | Keep the split (current design) | Collapse to agent-only |
   |---|---|---|
   | Schema | Two schemas, two directories, non-overlapping discovery (§1) | One schema; Condor is just `agents/condor/` (or equivalent) |
   | Coordinator's tools | None by design — routes, doesn't execute (§1) | Needs its own `tools` allowlist; likely the union of everything, undermining the "focused tools per agent" safety property that gives domain agents their current isolation |
   | White-labeling | One layer down: agent/strategy, per §11c | Anyone can spin up their own full "Condor," not just a domain agent under a fixed coordinator — a strictly more flexible white-label story, closer to the original "people build their own assistants" framing this roadmap is revisiting |
   | Interacts with #3 (recursion) | Recursion stays agent-to-agent; Condor's routing stays a special top layer | Recursion becomes fully symmetric — no distinguished root, any agent can reach any agent it's allowed to |
   | Risk | Leaves the current, working mental model unchanged | Real architecture churn across `condor/agents/agent.py`, `condor/memory/paths.py`'s hard-coded `agent_slug=None` special case, and the entire routing-decision-tree prompt in `assistants/condor/AGENT.md` |

   Recommendation for *how* to decide, not *what* to decide: make this call
   early in Phase 1, before items #1–4 above are implemented, since all of
   them (routine sharing, skill sharing, recursion, agent templates) get
   built differently depending on the answer. Note that collapsing the
   split does **not** have to change business-strategy.md §11b's conclusion
   that the *strategy* layer is the right tokenization unit — that
   conclusion survives either column in this table.

6. **Add pre-defined agent templates (consult- or delegate-capable), shipped
   with Condor as a starter kit** — this is what makes "more strategy
   creators building on the same engine" (§11a's premise) actually cheap for
   the next mandate after USDM, rather than a bespoke build every time:
   - **Data collection** — fetch/normalize market data and on-chain data
     across sources, feeding the "Condor can plug into data sources beyond
     Hummingbot" framing from the original business-strategy scoping.
   - **Back testing** — run historical backtests of a given strategy config.
   - **Forward testing** — paper-trade/dry-run in live markets without real
     capital; formalizes the `dry_runs/` folder that already exists per
     strategy (architecture doc §4) into an agent that drives it.
   - **Strategy creation** — the strategy-authoring sibling to
     `routine_builder`: helps draft a new `strategy.md` rather than a
     routine.
   - **Bot deployment** — wraps the `manage_controllers`/`manage_bots`
     deploy flow (the existing `pmm_mister_deploy` skill under
     `market_making_expert` is the prior art; this promotes the pattern to a
     standalone, reusable template).
   - **Routine builder** — already exists (`agents/routine_builder`).
   - **Routine scheduler** — a genuinely new capability, not just a
     template: routines today only run on-demand via `manage_routines`
     (architecture doc §3); there's no cron-like recurring execution short
     of wrapping one in a full `TickEngine` strategy. A scheduler fills that
     gap without requiring every recurring routine to become a strategy.

- **Hummingbot: add a Solana connector, single Docker instance.** Today
  Solana/DEX access goes through Gateway as a separate service alongside
  Hummingbot core and hummingbot-api — collapsing this to one Docker
  instance (with the new connector, and Swig support built in — see next
  item) directly reduces the per-session hosting footprint Phase 0 is
  costing out, and simplifies what Phase 2's hosted platform actually has to
  run per customer.
- **Condor: Swig integration.** This is where business-strategy.md §9b's
  Shape A actually gets built, not just validated in the abstract: productize
  the `feat/swig-solana-clean` branch (currently living in a separate
  Gateway fork) into Condor's real integration path, so a strategy's
  execution wallet can run under Swig's owner/delegate model against real
  Botcamp Solutions client capital.
- **Biz dev: decide Phase 2 or Phase 3.** This is business-strategy.md §7's
  Stage 2 fork, mapped onto this calendar: only once Shape A has actually
  run real client capital reliably (per the dogfooding above) does it make
  sense to choose between Phase 2 (traditional hosted SaaS, §7 option b) and
  Phase 3 (tokenized platform via MetaDAO or an Enzyme-style partnership,
  §7 options a/c). Not mutually exclusive forever — Phase 2 can run first
  and Phase 3 can follow — but this is the explicit decision gate, made with
  real Phase 1 evidence rather than speculatively now.

## Phase 1.5: Accumulated intelligence — the shared learnings store

business-strategy.md §13's new moat: an opt-in, cross-tenant corpus of
redacted strategy performance/learnings, queryable by any strategy (new or
existing) building on Condor. Sequenced between Phase 1 and Phase 2
specifically because it needs (a) Phase 1's dogfooded strategies to exist
as real contributors before there's anything worth aggregating, and (b) a
central Condor-operated service — a genuine departure from Phase 1's
single-tenant-VM-per-customer execution model (business-strategy.md §12),
but not a contradiction of it: §12's isolation argument is about
capital-bearing execution processes specifically; this service holds no
capital and no exchange API keys, only redacted text/stats, so it doesn't
carry the same blast-radius argument for single-tenancy. One central,
ordinary multi-tenant API service is the right shape here, not N single-
tenant boxes.

- **`share_learnings:` frontmatter flag on `strategy.md`** (architecture doc
  §4) — opt-in per strategy, off by default. When set, triggers a
  redaction-then-publish pipeline rather than raw `learnings.md` scraping:
  strip exact position sizes, wallet/account identifiers, and anything
  specific enough to leak a named client's operating parameters (Ripple,
  MetaDAO, USDM1 per business-strategy.md §1), before anything leaves the
  strategy's own store.
- **A new central "Condor Learnings" service** — separate from any given
  customer's hosted execution box, aggregating redacted submissions from
  every opted-in strategy (self-hosted or hosted). Two content tiers:
  coarse aggregate statistics (broadly available) and richer textual
  learnings (reserved for contributors, per §13's reciprocity design, to
  counter adverse selection — the strategies most likely to opt in
  otherwise are the ones doing worst, not the best).
- **Reputation weighting** — tag submissions by contributing-strategy
  tenure/uptime and whether they're backed by real capital vs. a dry
  run/backtest (architecture doc §4's experiment modes), so the corpus
  isn't diluted by noisy or paper-trading-only entries.
- **A new MCP tool, `query_shared_learnings(tags, agent_type, regime,
  ...)`** — callable from any strategy's `TickEngine` reasoning or a
  Condor/domain-agent chat session, a small and natural addition to the
  existing tool surface (`mcp_servers/condor/tools/`), not a new
  integration paradigm. Enables the cold-start use case directly: a new
  strategy on an unseen exchange/pair (or the next USDM-adjacent mandate)
  queries what other strategies have already learned instead of starting
  from zero.
- **Legal review before any paid tier**, separate from and in addition to
  §9's tokenization review: centrally aggregating and selling access to
  cross-user trading performance/learnings data can implicate investment-
  adviser regulation, a different regime than §9's securities-law concern.
  Ship the opt-in/redaction/reciprocity design *before* collecting any
  data, not as a retrofit — the same "move fast before the guardrails
  exist" mistake pattern from §9d's Falcon lesson applies here too, with
  data leakage instead of a securities violation as the failure mode.
- **Herding-risk mitigation, designed in from the start**: keep each
  strategy's own LLM reasoning in the loop to interpret and contextually
  apply a shared learning rather than mechanically triggering on it, and
  consider staggering how quickly newly published learnings propagate into
  live decision loops — many hosted strategies reacting identically and
  simultaneously to the same shared signal is a new systemic-risk category
  this feature introduces that the private-journal model didn't have.
- **Ties directly into Phase 2's pricing** (below): read access to the full
  contributor-tier corpus is a natural premium upsell — "the more
  strategies run on Condor, the smarter every new one starts" — and a
  flywheel a fork can't replicate on day one regardless of code parity.

## Phase 2: 1Q 2027 — hosted platform, like Nous

The traditional-SaaS fork from business-strategy.md §7 option (b), fleshed
out to the same level of detail as Nous's Portal (§2's comparable).

- **Include LLM and hosting** in the subscription — the bundled-cost model
  from §5 ("the subscription... covers the underlying LLM calls"), now
  informed by Phase 0's actual cost research rather than a placeholder tier
  price.
- **API tokens** — a surface not previously specced in §5's tiers: expose
  Condor's hosted agents programmatically, not just through the chat UI,
  mirroring how Nous Portal offers API access alongside its own chat
  interface. This is what lets a power user or a Botcamp Solutions client's
  own internal tooling integrate directly rather than going through Telegram
  or the web dashboard.
- **Monetize: builder codes + subscription, per-bot-instance, private box
  (enterprise).**
  - **Builder codes** — a referral/attribution mechanism for third-party
    agent or strategy builders, modeled on the standard Solana DeFi pattern
    (Jupiter's referral/platform-fee program is the canonical example): a
    builder embeds a code, and earns a cut of the fees generated by capital
    routed through what they built. This is the concrete mechanism that
    would make business-strategy.md §9e's two-layer fee model real for
    third-party strategy creators specifically, ahead of any full
    tokenization step.
  - **Subscription** — the tiered pricing already specced in §5.
  - **Per-bot-instance** — usage-based pricing that scales with the number
    of deployed bot instances, i.e. the "extra concurrent strategies beyond
    the tier cap" add-on in §5, now named as its own line item.
  - **Private box (enterprise)** — a dedicated, single-tenant hosted
    deployment rather than shared multi-tenant infrastructure, giving
    concrete technical shape to §5's "Enterprise / custom" tier for
    security/compliance-sensitive institutional clients (consistent with
    §3b's "institutional later, not from day one" caveat — this is where
    "later" actually lands). Per §12's Devin/Cognition research, this
    single-tenant-per-customer model isn't actually an enterprise-only
    upsell — it's the right default architecture for *every* hosted tier,
    since Condor's stage doesn't support (and per §12, doesn't need) the
    shared multi-tenant isolation investment Cognition describes building
    for its own cloud-agent product. "Private box" language stays reserved
    for Enterprise as a dedicated-infra-tier *marketing* distinction
    (dedicated support/SLA/compliance exports), not a different isolation
    model from Starter/Pro/Team underneath.

## Phase 3: Maybe — tokenized agents on Solana

The ambitious fork from business-strategy.md §7 option (a)/(c) and §9,
only reached if Phase 1's biz-dev gate points here instead of (or in
addition to) Phase 2.

- **MetaDAO ICO** — per §9f/§7, still gated on the full securities-law
  review §9d demands (the CoinAlpha Falcon precedent), and still evaluated
  against the Enzyme-partnership alternative (§9e) that reaches the same
  capital/liquidity moat (§10) without a public token event.
- **Tokenizable agents** — per §11b, tokenize at the **strategy** layer, not
  the agent or assistant layer: a strategy already has its own isolated
  config, session journal, and track record, making it the right unit for a
  Swig-secured wallet plus an investor-claim token, one per strategy.
- **Solana focus** — consistent with where Swig, Phase 1's Solana connector
  work, and the GLAM/Enzyme comparables in §9e all already sit.
- **Strategies bake in Swig policies** — the concrete file-format follow-
  through of §11b: extend `strategy.md`'s frontmatter (architecture doc §4)
  with a native `swig_policy:` block (allowlisted programs, per-mint spend
  caps) alongside the existing `default_config:`, so a strategy's Swig
  guardrails are authored as a first-class part of the strategy itself
  rather than bolted on separately in deployment tooling.

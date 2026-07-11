# Usage patterns — mapping real work onto routines, skills, and agents

Written while building the first post-refactor validation examples
(2026-07-11): a backtesting capability and a data-collection capability.
This is the decision framework for "should this be a routine, a skill, or an
agent?", applied to the two requests users actually make first.

## The decision framework

The three primitives answer three different questions:

| Primitive | Answers | Properties |
|---|---|---|
| **Routine** | *how, exactly* | Deterministic Python; config schema; cheap, repeatable, schedulable; produces reports/structured output; no LLM in the loop |
| **Skill** | *how, in principle* | Markdown procedure an LLM follows and adapts; progressive disclosure; shareable across agents (tiers); improvable by the curation loop |
| **Agent** | *who owns it* | Identity + accumulated learnings + attributed sessions + track record; consultable (Q&A), delegable (background task), loopable (scheduled playbook) |

Rules of thumb that fall out of this session's experience:

1. **If a step can be deterministic, it must be a routine.** An LLM fetching
   candles or looping a fill simulation is slower, costlier, and less
   reproducible than 40 lines of Python. (The repo's own history agrees: the
   user's `spcx_*_backtest.py` family predates all of this — hand-written
   backtest routines were already the natural form.)
2. **The judgment layer is a skill, not prose in an agent body.** "Which
   windows count", "when is a result overfit" is know-how — it belongs where
   the curation loop can improve it and other agents can inherit it, not
   duplicated in every playbook (the drift lesson of refactor-05 §1).
3. **Make it an agent when the work accumulates.** An agent earns its
   existence by having learnings worth keeping, sessions worth attributing,
   or a consult surface other people/agents use. A capability used once
   stays a routine + skill under an existing agent.
4. **A loop (strategy) only when the work is *scheduled*.** Consult and
   delegate cover "on demand"; ticks are for standing orders.

## Case 1: Backtesting

**Ground truth discovered while building** (matters for any design):
platform backtests (`run_backtest` on saved controller configs) work only
where the venue's data feed is reachable — on this deployment binance is
geo-blocked (HTTP 451) and hyperliquid has no backtest connector. Candle
fetching works everywhere. So the always-available path is
**candle-simulation in routines**; the platform engine is an optimization
where it works.

**Approaches considered:**

- *A pure routine* (`grid_backtest`): reproducible and cheap, but a routine
  can't decide whether the result is trustworthy, compare variants
  meaningfully, or remember that a parameter family always overfits.
  Sufficient for a one-off; insufficient as a capability.
- *A skill on an existing agent* (e.g. "backtest before deploy" on
  mm_expert): right for the *integration point*, but backtesting knowledge
  (windows, fee realism, overfit guards) serves every future trading agent,
  and its learnings ("hyperliquid candle sims underestimate queue effects")
  deserve their own pool — pinning it to one agent recreates the
  knowledge-has-no-home problem.
- *A loopable backtest agent* (tick = "keep backtesting"): wrong — there is
  no standing order; backtests happen on demand.

**Chosen shape — an agent that owns routines and a skill, with no loop:**

```
agents/backtest_lab/
  AGENT.md                     # identity: measures, never trades
                               # risk_limits: {0, 0} — the read-only baseline
  skills/backtest-methodology/ # windows, fees, overfit guards, report format
  routines/grid_backtest.py    # deterministic candle-sim (authored BY
                               # routine_builder via delegation)
  sessions/                    # consults + delegations = the lab notebook
```

- **Zero risk baseline** (`max_position_size_quote: 0, max_open_executors:
  0`): the same pattern validated on funding_rate_watcher — the agent gets
  live market data but the risk gate blocks any order-shaped action by
  construction, so "never trades" is mechanical, not aspirational.
- **Usage:** `consult(agent="backtest_lab", task="is a 2% grid on SOL worth
  running?")` for interactive judgment; `delegate(...)` for parameter sweeps
  (long-running, unattended, transcripted). Both leave sessions; learnings
  accumulate; the curation loop folds repeated observations into the
  methodology skill.
- **Integration with mm_expert:** the *chat* orchestrates ("backtest it,
  then deploy") — agents stay single-purpose. If backtest methodology proves
  broadly useful, it is a one-confirmation promotion to the shared tier.

## Case 2: Data collection

**Approaches considered:**

- *An agent with a collection tick* ("every 5 minutes, fetch and store
  funding"): an LLM invocation per fetch to do deterministic I/O — the
  clearest possible misuse of a tick loop. Rejected.
- *A dedicated collector agent*: justified only at the point where
  collection itself needs judgment at scale — universe selection, gap
  triage, venue-anomaly investigation across many datasets. Not yet.
- **Chosen: collection is a routine owned by the agent that consumes the
  data.** `funding_logger` lives under funding_rate_watcher and appends
  snapshots to `store/funding_history.csv` — the agent's store is exactly
  the "durable artifacts" home. Scheduling comes free from existing
  machinery (continuous routines / cron / a host harness scheduling
  `manage_routines(action="run")`); the owning agent's consults can then
  answer from *its own collected history* instead of only the live API, and
  its QC judgment ("this gap looks like an outage, not a delisting") is
  agent work layered over routine output.

The general shape: **routine collects → agent's store holds → agent
interprets → learnings/curation record the quirks.** A collector *agent*
gets created the day the collection portfolio itself needs a brain.

## What these examples validate (and what they surfaced)

Validated end-to-end: agent CRUD with a zero-trade baseline; chat-side skill
authoring into an agent's local tier; **routine authoring as a
routine_builder delegation** (the flagship flow — the system building its own
examples); consult over skill + routine; the sessions/learnings/curation
substrate ready to accumulate from real use.

Surfaced (facts, not bugs): venue geo-blocks make platform backtesting
deployment-dependent — the methodology skill encodes the fallback rule so
agents don't retry-loop against a 451; candle sims must self-label their
coarseness (no order-book queue) so results aren't over-trusted.

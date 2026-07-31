---
name: xrpl_mm_deploy
description: End-to-end playbook for standing up an XRPL CLOB maker — preflight checks,
  reserve/trustline setup, sizing, first quotes, and verification.
when_to_use: When asked to set up, deploy, or launch market making on an XRPL pair.
  Follow start to finish when running as a delegate task.
created: '2026-07-28T00:00:00Z'
source: agent:xrpl_market_maker
---

# XRPL Maker Deploy Playbook

The execution path defaults to **controller mode** — `xrpl_mm_feasibility` records why, and
Phase 3 below confirms it at deploy time. Read that skill first only if you need the full
reasoning, or are re-verifying after a Hummingbot/connector upgrade.

## Phase 1 — Preflight

**1. Confirm the pair is worth quoting.**

```
explore_geckoterminal(action="top_pools", network="xrpl")
```

Require real depth *and* real turnover. Reserve alone is not liquidity — FUZZY/XRP holds
~$1.6M against ~$50K daily volume (≈3% turnover, effectively dead capital). RLUSD/XRP is
currently the only XRPL pair with both.

**2. Verify the issuer's transfer fee is 0%.**

A non-zero transfer fee on the issued asset applies on top of everything and can exceed
the entire spread. Check the issuer's account settings before sizing. If it is non-zero,
subtract it from the spread ceiling — or do not quote.

**3. Confirm the account has XRPL credentials configured.** This gates controller mode and
is the most likely reason a deploy looks fine and then does nothing. The API's shared
*keyless* data connector is built with `trading_pairs=[]`, and the XRPL connector derives
trading rules only for the pairs it was constructed with — so every XRPL pair, including
the connector's own default, comes back "not found" and no order can be sized. An account
with XRPL credentials gets a real trading connector with pairs and sidesteps it. If
credentials are absent, stop and tell the user: this is not a controller-support verdict,
and executor mode will not rescue it either.

**4. Confirm balances and free reserve.**

```
get_portfolio_overview()
```

Needed: XRP for the base reserve (1 XRP) plus 0.2 XRP per intended offer, a trustline for
the issued asset, and inventory on both sides. **Reserved XRP is not spendable** — size
against free balance.

## Phase 2 — Spread viability

```
manage_routines(action="run", name="xrpl_mm_quote_planner",
                strategy_id="xrpl_market_maker.rlusd_xrp_maker",
                config={"xrpl_pair": "<pair>", "tick_interval_sec": <frequency_sec>,
                        "requote_interval_sec": <executor_refresh_time>,
                        "levels_per_side": <n>, "total_amount_quote": <capital>})
```

`requote_interval_sec` is required for a controller deploy. The floor scales with how long
a quote stays exposed, and in controller mode that is `executor_refresh_time` — the bot
requotes on its own clock. Pass `frequency_sec` by mistake and you get a `viable: false`
that is an artefact of your tick cadence, blocking a deploy that is genuinely viable.

Read the `VIABILITY` block. **A `viable: false` computed from the right interval means do
not deploy** — the adverse-selection floor has met the AMM fee ceiling. Shorten
`executor_refresh_time` or wait for volatility to fall. Never tighten below the floor to
force fills.

Also read `divergence_vs_reference_bps`. A large gap between the XRPL book and the CEX
reference means stale data far more often than it means opportunity.

## Phase 3 — Deploy

**Always attempt controller mode first.** Executor mode is a fallback for when the
controller attempt actually fails, not a coin-flip default — do not skip straight to
executors just because feasibility was "unverified" for this pair/session.

### Controller mode (default — try this first)

Use `pmm_simple` — `pmm_dynamic` needs a NATR/MACD candles feed that XRPL does not have.
Its perpetual-flavoured defaults are inert on a spot connector, so set `leverage=1` and
leave `stop_loss`/`take_profit`/`time_limit`/`trailing_stop` `null`; do not read those
defaults as a rejection.

**Four defaults are actively wrong for XRPL. Set all four explicitly:**

| Field | Default | Set to | Why the default breaks it |
|---|---|---|---|
| `executor_refresh_time` | `300` | `30` (from config) | Sets the quote's exposure window, so it sets the adverse-selection floor. At 300s the floor is ~22 bps against a ~10 bps AMM ceiling — never viable. |
| `skip_rebalance` | `false` | `true` | `check_position_rebalance` skips only perpetual connectors, so on `xrpl` it fires and submits an `ExecutionStrategy.MARKET` order to true up base inventory — crossing a thin book with a market order, which is the one thing a maker must not do. |
| `buy_spreads` / `sell_spreads` | `0.01,0.02` | planner's `controller_spreads` | These are **fractions of the reference price**, not bps. `order_price = reference_price * (1 ± spread)`. Pasting `22.4` from a bps field quotes at 2240%. |
| `total_amount_quote` | `100` | planner's `controller_total_amount_quote` | Denominated in the pair's **quote asset**. On RLUSD-XRP that is XRP, not USD — a USD figure oversizes by the XRP price and breaches the risk limit silently. |

Know what you are deploying: `pmm_simple` centres its ladder on the **`xrpl` connector's own
mid price** (`update_processed_data` reads `PriceType.MidPrice`), not on your CEX reference.
There is no config field to repoint it. Between your ticks the bot quotes off the on-ledger
mid — so `divergence_vs_reference_bps` is the number that tells you whether it is quoting
around a stale centre, and killing the switch is the response. If divergence is routinely
large on this pair, recommend executor mode rather than deploying a controller that cannot
see the reference.

Remember there are **two config stores** and updating one does not update the other:

```
manage_controllers(action="upsert", target="config", ...)   # saved template
manage_bots(action="deploy", ...)                           # live bot
```

Declare `max_global_drawdown_quote` within the session's risk limits on every deploy — in
the quote asset, same conversion as `total_amount_quote`. Deploy under the `bot_name` from
config (`rlusd-xrp-maker`), not a per-run name: a fresh name each restart orphans the
previous bot's resting offers on-ledger while their reserves stay locked.

**Confirm it actually worked before trusting it** — a config that merely *saved* is not
proof. Check `manage_bots(action="status")` and the on-ledger order book (Phase 4). Treat
the attempt as failed, and fall through to executor mode, only when you see one of:

- `manage_controllers(upsert)` rejects the config (schema validation error)
- `manage_bots(deploy)` fails, or the bot's status comes back `stopped`/errored shortly after
- The bot is running but places no orders on the XRPL ledger within a few ticks

Journal whichever of these happened as a `category="execution"` learning — this is the
feasibility answer for this session, not just a one-off glitch, so record it rather than
silently falling back every time.

### Executor mode (fallback — only after a real controller failure)

Place laddered LIMIT / LIMIT_MAKER offers, passing `controller_id` as a **top-level**
argument so PnL attributes to this agent:

```
manage_executors(action="create", controller_id="{agent_id}",
                 executor_config={"type": "order_executor", "connector_name": "xrpl", ...})
```

Start with **one level per side** and confirm it reaches the ledger before laddering.

## Phase 4 — Verify it actually worked

A clean tool response is **not** proof the offer exists on-ledger. Confirm:

```
get_market_data(data_type="order_book", connector_name="xrpl", trading_pair="<pair>")
```

Your offer should be visible at the expected price. If it is not:

- **`tecUNFUNDED_OFFER`** → sizing ignored reserved XRP. Recompute against free balance.
- **`tecNO_LINE` / `tecPATH_DRY`** → trustline missing or insufficient limit.
- **Offer accepted but invisible** → likely crossed and filled immediately. Check balances
  before re-placing, or you will double up.

## Phase 5 — Hedge decision (state it explicitly)

Unhedged XRP inventory dominates spread capture — XRP has moved 2.7% in an hour against
roughly 3 bps per fill. Decide and *tell the user which is active*:

- **Unhedged** — deliberate XRP exposure. Acceptable only if the user chose it knowingly.
- **Hedged** — neutralise net delta with a short on `bitget_perpetual`. Preferred. Check
  leg parity every tick; if one leg is missing, restoring it is the only action for that tick.

## Phase 6 — Journal

Write one action entry per tick. Record execution failures as `category="execution"`
learnings — XRPL trustline and reserve rejections recur and are cheap to avoid twice.

## Rollout

`dry_run` → `run_once` → `loop` with short `max_ticks` and conservative limits. XRPL
testnet exercises the mechanics but will not reproduce real book depth or competing
makers, so plan on small live size early rather than a long testnet phase.

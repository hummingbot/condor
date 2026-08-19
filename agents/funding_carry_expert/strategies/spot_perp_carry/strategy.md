---
name: Spot Perp Carry
description: Delta-neutral long spot / short perp funding carry on Bitget — screens for
  holdable carry, opens matched legs, and holds continuously.
agent_key: null
skills:
- funding_carry_deploy
default_config:
  frequency_sec: 3600
  total_amount_quote: 200
  execution_mode: dry_run
  spot_connector: bitget
  perp_connector: bitget_perpetual
  trading_pair: ''
  min_volume_usd: 5000000
  min_stability_pct: 65
  min_net_annualised_pct: 3.0
  max_concurrent_carries: 2
  leverage: 3
  parity_tolerance_pct: 5
  unwind_trailing_periods: 90
  risk_limits:
    max_position_size_quote: 250
    max_open_executors: 6
    max_drawdown_pct: 8
    shutdown_drawdown_pct: 15
default_trading_context: ''
created_at: '2026-07-29T00:00:00Z'
---

# Spot Perp Carry

You hold **delta-neutral funding carry**: long spot, short perpetual, matched notional, on
one venue. The position has no directional exposure. Its entire return is the funding the
short perp leg collects, so your job is mostly to open it well and then *leave it alone*.

## Configuration at launch

Read all runtime values from `[CURRENT CONFIG]`:

`spot_connector` · `perp_connector` · `trading_pair` · `total_amount_quote` ·
`min_volume_usd` · `min_stability_pct` · `min_net_annualised_pct` ·
`max_concurrent_carries` · `leverage` · `parity_tolerance_pct` · `unwind_trailing_periods`

If `trading_pair` is empty, select candidates via the screener. If it is set, operate only
that pair.

Default `frequency_sec` is 3600 — this is a **low-turnover strategy** and there is nothing
useful to do minute by minute.

## Each tick

**1. Check parity first — before anything else.**

```
manage_executors(action="positions_summary")
```

For each open carry, compare spot and perp notional. If they differ by more than
`parity_tolerance_pct`, **fixing parity is the only action this tick.** Restore the missing
or undersized leg, or close the orphan. Then stop.

A one-legged carry is a naked directional position — strictly worse than either holding or
being flat.

**2. If parity is clean and a carry is open → HOLD.**

Log accrued funding and stop. Read the hold policy below before considering anything else.

**3. If capacity remains (`< max_concurrent_carries`) → screen.**

```
manage_routines(action="run", name="funding_screener",
                strategy_id="funding_carry_expert.spot_perp_carry",
                config={"min_volume_usd": <config>, "require_spot": true,
                        "min_stability_pct": <config>, "top_n": 10})
```

Open a new carry only if the top candidate satisfies **all** of:

- `sign_stability_pct` ≥ `min_stability_pct`
- `net_hold_annualised_pct` ≥ `min_net_annualised_pct`
- `days_to_cover_taker_cost` well below your intended hold
- `volume_24h_usd` ≥ `min_volume_usd`
- a spot market exists (the screener enforces this — never disable it)

Otherwise **HOLD** and journal that nothing qualified. That is a normal outcome.

**4. Journal** one action entry per tick.

## 🚨 Hold policy — the rule that decides whether this works

**Never close a carry because funding printed negative.**

| pair | buy-and-hold | closing on sign flips |
|---|---|---|
| BTC | **+46 bps** | **−371 bps** |
| ETH | +79 bps | −168 bps |
| XRP | +53 bps | −213 bps |

Funding is negative 21–31% of periods and flips sign 52–93 times per 270 periods, but the
negative prints are small. Each exit costs a full round trip (~21 bps taker) to dodge a
fraction of a basis point.

| Observation | Correct action |
|---|---|
| One negative funding print | **HOLD** |
| Several negative prints in a row | **HOLD** |
| Price moved sharply | **HOLD** — you are delta-neutral. Verify parity. |
| Unrealised PnL on one leg looks alarming | **HOLD** — the other leg offsets it. Check parity, not PnL. |
| Trailing mean over `unwind_trailing_periods` turned negative | Consider unwinding |
| Delisting announced on either leg | Unwind |
| Risk limit breached | Unwind |

The urge to manage this position on individual prints is the main way it loses money.

## Sizing

- Both legs sized in **matched notional**, not matched quantity.
- Per carry: `total_amount_quote / max_concurrent_carries`.
- Total exposure across carries must stay within `max_position_size_quote`.
- Keep `leverage` low. The perp leg must survive drawdown without liquidation — a
  liquidated hedge converts a neutral position into a naked long at the worst moment.
- Prefer maker fills. Taker on all four legs is ~21 bps versus ~8 bps maker; on a 3% APR
  carry that gap is roughly two weeks of return.

## Expected returns — calibrate against these

| universe | realistic net |
|---|---|
| Majors (BTC/ETH/XRP/SOL) | **1–4% APR** |
| Screened high-carry names with spot | **~10% APR** |

If a projection materially exceeds these, you are probably annualising a snapshot rather
than a mean. Always size and report from `funding_mean_bps`, never `funding_now_bps`.

## Guardrails

- **Never** open a perp leg without the matching spot leg available — no spot means no
  hedge and no carry.
- **Never** disable `require_spot` in the screener.
- **Never** size from the current funding print. Use the trailing mean.
- **Never** close on individual negative prints.
- **Never** call `place_order` — use `manage_executors` only.
- Pass `controller_id="{agent_id}"` as a top-level argument to `manage_executors`.
- Open the perp leg **first** (margin rejection is the likelier failure); close the spot leg
  **last** (shed leverage risk first).
- Do not open a carry you do not intend to hold for weeks. Short holds lose to fees by
  construction.

## Error recovery

| Tell | Meaning | Action |
|---|---|---|
| Perp create fails, spot filled | Naked long spot | Close spot immediately or complete the hedge — highest priority |
| Insufficient margin | Leverage or size too high | Reduce size; do not raise leverage to force the fit |
| Position appears on one connector only | Leg parity broken | `positions_summary`, then restore or flatten |
| Screener returns no candidates | Filters correctly rejected everything | HOLD and journal — a normal outcome |

On create failure: fetch the schema, fix fields, retry **once**, then journal a
`category="execution"` learning. Never loop retries within a tick.

## Rollout

Ships as `execution_mode: dry_run`. Progress to `run_once`, then `loop`. Bitget paper
trading is available and should be used before live capital.

Note this strategy needs **weeks** to demonstrate anything — carry accrues in fractions of
a basis point per 8h. A short live test proves the plumbing works, not that the strategy
works.

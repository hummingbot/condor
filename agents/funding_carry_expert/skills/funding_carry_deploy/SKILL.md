---
name: funding_carry_deploy
description: End-to-end playbook for opening and maintaining a delta-neutral spot-perp
  funding carry — screening, sizing, leg parity, and unwind.
when_to_use: When asked to set up, deploy, or launch a funding carry / cash-and-carry /
  spot-perp basis position. Follow start to finish when running as a delegate task.
created: '2026-07-29T00:00:00Z'
source: agent:funding_carry_expert
---

# Funding Carry Deploy Playbook

## Phase 1 — Screen

```
manage_routines(action="run", name="funding_screener",
                strategy_id="funding_carry_expert.spot_perp_carry",
                config={"min_volume_usd": 5000000, "require_spot": true,
                        "min_stability_pct": 65, "top_n": 10})
```

Read the ranked candidates. What each field is for:

| Field | Use |
|---|---|
| `sign_stability_pct` | **First filter.** Below ~65% the sign flips too often to hold. |
| `funding_mean_bps` | **Size on this**, never on `funding_now_bps` — a snapshot overstates carry by ~2.5×. |
| `net_hold_annualised_pct` | Realistic expected return, already net of one round trip. |
| `days_to_cover_taker_cost` | If this exceeds your intended hold, **do not open**. |
| `volume_24h_usd` | Must support both legs without moving the market. |

**Never override `require_spot`.** Without a spot market there is no hedge, and the
position is a naked perp — a different strategy with unbounded directional risk.

Also read the `REJECTED — unstable sign` block. Those are high-funding names that lose
money; they exist in the output specifically so they are not mistaken for opportunities.

## Phase 2 — Verify tradability

```
get_portfolio_overview()
get_market_data(data_type="funding_rate", connector_name="bitget_perpetual", trading_pair="<pair>")
get_market_data(data_type="order_book", connector_name="bitget", trading_pair="<pair>")
```

Confirm: quote balance covers the spot leg; margin covers the perp leg; both books are deep
enough for your size; the live funding sign matches the screener's dominant side.

If the live sign is *opposite* the historical dominant side, that is not a reason to flip —
it may be a normal flip within a stable regime. Size on the mean, and note the divergence.

## Phase 3 — Open both legs

Order matters. **Open the perp leg first**, because margin rejection is the likelier
failure and an unhedged spot position is worse than an unfilled one.

```
manage_executors(action="create", controller_id="{agent_id}",
                 executor_config={"type": "position_executor",
                                  "connector_name": "bitget_perpetual", ...})   # short
manage_executors(action="create", controller_id="{agent_id}",
                 executor_config={"type": "position_executor",
                                  "connector_name": "bitget", ...})             # long spot
```

**Match notional, not quantity** — the legs must neutralise in USD terms. Use maker orders
where the fill is not urgent; taker on all four legs costs ~21 bps versus ~8 bps maker, and
on a 3% APR carry that difference is roughly two weeks of return.

## Phase 4 — Confirm parity before doing anything else

```
manage_executors(action="positions_summary")
```

Both legs must exist with matched, opposite notional. **If only one leg opened, fixing that
is the only permitted action** — either complete the missing leg or close the orphan. A
one-legged carry is a naked directional position.

## Phase 5 — Hold

This is the phase where the strategy is won or lost, and the correct behaviour is to do
nothing:

- Funding printed negative once → **hold**. Expected 21–31% of the time.
- Funding printed negative three times → **hold**. Still noise.
- Trailing 30-day mean funding turned negative → *now* consider unwinding.
- Price moved a lot → **irrelevant**, you are delta-neutral. Verify parity, then hold.

Closing on individual negative prints turns a positive strategy into a large loss
(measured: BTC +46 bps held vs −371 bps gated). Each tick, confirm parity, log the accrued
funding, and stop.

## Phase 6 — Unwind

Only on a genuine trigger: sustained regime inversion, a delisting announcement on either
leg, a risk-limit breach, or an explicit user request.

Close **both legs together**. If they cannot close simultaneously, close the *spot* leg
last — the perp leg carries leverage and liquidation risk, so shed that first.

Verify balances afterwards. A clean executor status is not proof the position is flat.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Position earns far less than screened | Sized on `funding_now_bps` | Re-size on `funding_mean_bps` |
| Losing money despite positive funding | Exiting on sign flips | Hold continuously |
| Sudden directional PnL | A leg closed or was liquidated | Check parity immediately; restore or flatten |
| Perp leg liquidated | Margin too thin for the hedge | Lower leverage; the hedge must survive drawdown |
| Carry never covers costs | Hold too short | Check `days_to_cover_taker_cost` before opening |

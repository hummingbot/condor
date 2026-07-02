---
name: capital_allocation
description: How total_amount_quote and initial_positions define a controller's isolated
  capital — the budget a single pmm_mister controller trades with, and how to seed it
  with base assets you already hold so each controller is independent from the wider portfolio.
when_to_use: When sizing a controller, deciding total_amount_quote, splitting one market
  into several controllers, or when the user already holds the base asset (spot) and wants
  to fund the strategy with existing inventory instead of buying fresh. Also read this
  before answering "how much capital does this bot use" or "how do I use the BTC I already have".
source: agent:market_making_expert
---

# Capital Allocation: total_amount_quote & initial_positions

This skill explains the two knobs that define **how much capital a single
controller trades with** and **which of that capital comes from assets you
already hold**. Together they make each controller's book independent from the
rest of your portfolio.

---

## 1. `total_amount_quote` — the controller's budget

`total_amount_quote` is the **total capital assigned to that one controller**,
denominated in the quote asset (e.g. USDC for a BTC-USDC market, BRL for a
BTC-BRL market).

This is the reference amount everything else is measured against:

- `portfolio_allocation` — fraction of `total_amount_quote` actively deployed
  per iteration.
- `target_base_pct` / `min_base_pct` / `max_base_pct` — the inventory band. These
  percentages are **percentages of `total_amount_quote`**, expressed in quote
  value. If `total_amount_quote = 2000` and `target_base_pct = 50`, the target
  base inventory is worth **$1,000** of the base asset.

So `total_amount_quote` is the denominator. Change it and every absolute
position size, order size, and inventory band scales with it. It is the single
number that says "this controller is allowed to work with this much money."

---

## 2. The default: starting fresh from quote

By default a controller assumes it starts with **`total_amount_quote` worth of
quote asset and zero base**. It then builds up base inventory toward
`target_base_pct` by getting its buy orders filled — buying the base with quote
as the market comes to it.

This is fine when you have plenty of quote and don't mind the controller
acquiring the base itself. Nothing extra is needed.

---

## 3. `initial_positions` — seeding with assets you already hold

If you are trading **spot and you already own the base asset**, you don't have to
make the controller buy it from scratch. You can hand existing inventory to the
controller at startup via `initial_positions`:

```yaml
initial_positions:
  - amount: 0.08328
    connector_name: binance
    side: BUY
    trading_pair: BTC-BRL
```

When you deploy through the normal flow, configs are upserted as a `config_data`
**dict** (`manage_controllers(action="upsert", target="config", ...)`), so
`initial_positions` is a **list of dicts** — the copy-paste-ready form is:

```json
"initial_positions": [
  {
    "amount": 0.08328,
    "connector_name": "binance",
    "side": "BUY",
    "trading_pair": "BTC-BRL"
  }
]
```

Both forms are equivalent — YAML for config files, JSON/dict for the
`manage_controllers` upsert path.

What this means:

- You declare **how many units of the base asset from your portfolio you want to
  assign to this controller at the start** of the strategy.
- That amount goes **directly into position hold** — the controller starts
  already holding this inventory as an open BUY position, instead of holding pure
  quote.
- `side: BUY` marks it as a long base position that the strategy now manages
  (its TP/SL and inventory logic apply to it just like a position it opened
  itself).

### You choose whether to use existing assets or not

- **Don't assign them** → the controller starts fresh from quote. Use this when
  you have enough quote to fund `total_amount_quote` on its own and you'd rather
  leave your existing base untouched. You can hold assets and simply not use
  them.
- **Assign them via `initial_positions`** → the controller starts with that base
  inventory already in hand, counting toward its `target_base_pct` band. Use this
  when you want your existing holdings to be the working inventory rather than
  buying more.

The base you assign should be consistent with the controller's budget: the quote
value of the assigned base is part of the `total_amount_quote` this controller
manages, so it counts toward the inventory band (`target/min/max_base_pct`).

---

## 4. Why this matters: portfolio independence

This is the mechanism that makes **each controller's book independent from the
overall portfolio**. Instead of one giant strategy over your whole balance, you
carve the portfolio into slices and hand each slice to its own controller with
its own `total_amount_quote` and its own seeded inventory.

### Worked example — splitting a market into 10 controllers

Say you hold **$10k USDC and $10k of BTC** (dollar value) and want to market-make
BTC-USDC. You can deploy **10 controllers**, each with:

- `total_amount_quote = 2000` (2,000 USDC of budget per controller → 10 × 2,000 =
  $20k total, matching your combined capital).
- A proportional slice of your existing BTC assigned via `initial_positions` —
  i.e. split the BTC you hold across the 10 controllers so each starts with
  ~1/10 of it as seeded base inventory.

Now each controller runs its own isolated book on a $2,000 budget, half funded by
quote and half by the BTC you already had. The controllers don't fight over one
shared balance — each has a fixed, known slice, so their PnL, inventory bands,
and risk are measured independently. **This is how we make the strategy's capital
independent from the general portfolio.**

---

## Quick reference

| Concept | Meaning |
|---------|---------|
| `total_amount_quote` | Total capital assigned to **this one controller**, in quote units. The denominator for allocation and all base-pct bands. |
| `target/min/max_base_pct` | Inventory band as a % of `total_amount_quote`. |
| Default start | Controller assumes `total_amount_quote` in quote, 0 base — buys base itself. |
| `initial_positions` | Seed the controller with base you already hold; goes straight to position hold as a managed BUY. Optional. |
| Splitting a market | Deploy N controllers, each with `total_amount_quote = budget/N` and a proportional slice of existing base via `initial_positions` → N independent books. |

### `initial_positions` fields

- `amount` — units of the **base** asset to assign (e.g. `0.08328` BTC).
- `connector_name` — the connector holding the asset (e.g. `binance`).
- `side` — `BUY` for a long base position the strategy will manage.
- `trading_pair` — the controller's pair (e.g. `BTC-BRL`).

Only applies to **spot** with existing base inventory. It's optional — assign
existing assets when you want them to be the working inventory; omit it to start
fresh from quote.
</content>
</invoke>

---
name: Derive Flow Trader
description: Directional perp trader on Derive (derive_perpetual). Takes LONG/SHORT on SOL/USDC where capital-flow conviction is decisive and regime-aligned; bounded leverage, position-hold risk. Tested on Derive mainnet only.
# Model: runs on opencode-go via PR #175's custom endpoint "opencode"
# (custom@opencode:deepseek-v4-flash) — base https://opencode.ai/zen/go/v1,
# key = OPENCODE_GO_API_KEY (set via Settings -> LLM Endpoints or CUSTOM_LLM_* env).
agent_key: custom@opencode:deepseek-v4-flash
skills:
  - smart_money_flow:smart_money_playbook
default_config:
  execution_mode: loop
  frequency_sec: 300
  total_amount_quote: 50
  # Conservative default sizing (assumes a 50 USDC balance). Demo wallets may be
  # funded with more — size from the live portfolio balance
  # (get_portfolio_overview) rather than assuming 50. The wallet cap below is
  # enforced by the Risk Engine's gate (max_position_size_quote).
  min_order_amount_quote: 10        # smallest order placed per attempt
  max_ticks: 0
  risk_limits:
    max_position_size_quote: 50     # enforced by the Risk Engine; never exceed the funded wallet
    max_drawdown_pct: 8
    max_open_executors: 1           # one position at a time on a tiny wallet
    max_leverage: 2                  # conservative; notional scales with wallet
default_trading_context: |
  Trade SOL/USDC perpetuals on Derive (connector `derive_perpetual`) — the ONLY
  market this strategy trades. IMPORTANT: Derive perps are quoted in **USDC**,
  not USDT — use `SOL-USDC` (the connector's trading-rule map and order-book
  subscription both require the `-USDC` form). Before sizing any order, read the
  connector's live trading rules to confirm the current minimum order size (on
  Derive the SOL-USDC minimum is 0.1 SOL, ~$16 at current prices); an order
  below the minimum fails immediately (executor `close_type: FAILED`, "Open order
  failed"), so always size above the minimum and within the risk limits. Size the
  position from the live portfolio balance (`get_portfolio_overview`) — the Risk
  Engine enforces `max_position_size_quote` (50) against quote exposure, so pass
  `total_amount_quote` with the create (see call shape). One-time setup: in the
  Hummingbot client run `connect derive_perpetual` (wallet address + private key +
  subaccount id),
  then point this Condor instance at that running bot via the configured server.
  The Condor/API layer drives an already-connected instance — it does NOT add
  keys itself (security boundary; see mcp_servers/hummingbot_api/server.py).
  VALIDATION FIRST: connect `derive_perpetual` (mainnet) via the web dashboard
  (Settings → Keys) using a dedicated wallet funded with USDC on Base (native
  USDC). default_config is a conservative starting point: total budget 50, one
  position at a time (max_open_executors: 1), 2x leverage, min order 10 USDC —
  scale to the actual wallet funding after a clean run. NOTE: Condor's web UI
  filters out testnet connectors (see validation.md), so validation is
  mainnet-with-small-size, not testnet. Read the
  onchain_flow routine every tick; DEMO MODE: take LONG when an asset's
  flow_score >= +0.05 and SHORT when flow_score <= -0.05, in ANY regime
  (RISK-ON / RISK-OFF / NEUTRAL) — direction is the sign of the flow. If no
  asset clears |flow| >= 0.05, still take the asset with the largest |flow|
  (unless all three are ~0, |flow| < 0.02). Do not HOLD while a signal exists.
  The on-chain signal is Solana
  DeFi flow (GeckoTerminal), not XRPL.
---

# Derive Flow Trader — Playbook (what to do, step by step)

You are the **loop strategy** for the Smart-Money Flow agent. Your identity,
edge, and risk philosophy live in the agent file; this playbook is the exact
procedure for every tick. Follow it precisely — the risk gate enforces the
limits, so your job is to read the flow and size honestly.

## Step 1 — Run the flow read

Call `manage_routines(action="run", routine="onchain_flow")`.

It returns:
- a **direction** (`LONG` / `SHORT` / `HOLD`),
- the **best-flow asset**,
- the **Solana on-chain pulse**,
- a per-asset context table, and writes a ReportBuilder dashboard.

Read its output; do not re-fetch raw data.

## Step 2 — Filter (DEMO MODE: take a position unless flat-risk)

Trade **SOL-USDC only** — the only market this strategy trades. The routine may
flag other symbols; they are advisory at most, never tradeable here. With no
open position:

- **`LONG`** when SOL-USDC `flow_score >= +0.05` — any regime
  (ignore RISK-ON / RISK-OFF / NEUTRAL).
- **`SHORT`** when SOL-USDC `flow_score <= -0.05` — any regime.
- **Fallback:** if no asset clears `|flow| >= 0.05`, open on the asset with the
  largest `|flow_score|` anyway (direction = sign of flow).
- **HOLD** only when **all** |flow| < 0.02, or a position is already open.

DEMO MODE reasoning: a flat session is the failure mode for the demo. Prefer
opening the largest-|flow| asset (direction = sign of flow) so a position
exists — survival still beats activity, but only a genuinely flat market
(~0 flow) justifies standing aside.

## Step 3 — Size & enter

One position at a time (`max_open_executors: 1`), **2x leverage**, sized from
the live portfolio balance within `max_position_size_quote` (50). Never exceed
the funded wallet. Open a **`PositionExecutor`**.

### Call shape (REQUIRED — matches the risk gate)

- Put `"controller_id": "<Agent ID from the system prompt>"` **INSIDE**
  `executor_config` — the gate reads the tag ONLY from inside the config.
- Pass **`total_amount_quote`** (the quote notional, e.g. ~$16 for 0.1 SOL at
  current price) **AND** `amount` in **BASE units**: **0.1 SOL** (= Derive's min
  order). The gate compares `total_amount_quote` against the $50 cap, so give it
  the honest quote notional of the position.
- Set `leverage: 2`, `side: 1` (LONG) / `2` (SHORT),
  `connector_name: "derive_perpetual"`, `trading_pair: "SOL-USDC"`, plus a
  `triple_barrier_config` (TP/trail/stop per Step 4).

The Risk Engine auto-blocks anything over the $50 cap — do not fight it, resize.

## Step 4 — Manage open positions

- **Take profit:** scale out 50% at +2%, trail the rest with a +1.5% activation
  and 2% trail; hard stop at −2.5%.
- **Signal flip:** if the next tick's flow read inverts (score crosses through
  zero against your position) with conviction ≥ 0.4, exit and optionally reverse.
- **Time limit:** max 8h hold per position.
- **Leftover position:** if a grid stops out but holds inventory, wait for a
  recovery within 1% of breakeven, then exit with an `OrderExecutor`.

## Step 5 — Journal the *why* in flow terms

One line per tick, in flow terms, e.g.:

- *"RISK-ON; SOL flow +0.52 (vol/mcap 2.1x, trending #4); Solana pulse +0.44 →
  LONG SOL-USDC."*
- *"RISK-OFF; SOL flow −0.4 → SHORT SOL-USDC."*

Record the thesis, not just the fill.

---

## Cheat sheet (every tick)

| # | Action | Key values |
|---|---|---|
| 1 | Run `onchain_flow` routine | direction + best asset + pulse + table |
| 2 | Filter | SOL-USDC only; LONG ≥ +0.05, SHORT ≤ −0.05 (any regime); fallback largest-|flow|; HOLD only if all < 0.02 |
| 3 | Size & enter | 1 executor, 2x lev, ≤ $50 quote; controller_id inside config; total_amount_quote + 0.1 SOL base |
| 4 | Manage | TP 50% @ +2%, trail 1.5/2%, stop −2.5%, 8h max, flip on conviction ≥ 0.4 |
| 5 | Journal | one flow-thesis line per tick |

If the read is ambiguous, prefer the fallback open (DEMO MODE) — a flat session
is the failure mode. No forced trades outside the playbook.

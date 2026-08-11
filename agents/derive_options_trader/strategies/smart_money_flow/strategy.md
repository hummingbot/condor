---
name: Smart Money Flow
description: Directional perp strategy on Derive (derive_perpetual). Reads cross-market capital flow (regime + Solana on-chain pulse) and confirms it against Derive options positioning before taking LONG/SHORT on SOL/USDC; bounded leverage, position-hold risk. Tested on Derive mainnet only.
agent_key: claude-acp:sonnet
skills:
  - derive_options_trader:smart_money_playbook
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
  The on-chain signal is Solana DeFi flow (GeckoTerminal), not XRPL.
  Also read the options_flow routine each tick and use its composite_score as a
  confirmation/sizing input: full size when options agree with the flow
  direction, half size when they strongly disagree (|composite| >= 0.40 against
  the flow), and use the options direction as tie-breaker when the flow read is
  ambiguous.
---

# Smart Money Flow — Playbook

You are the **smart-money capital-flow strategy** of the Derive Options Trader agent,
trading **perpetuals on Derive** (`derive_perpetual`). Your primary signal is **where
capital is moving** — the cross-market + on-chain composite from `onchain_flow` —
**confirmed against Derive options positioning** from `options_flow`.

**Motto:** *"Follow the flow, not the chart."*

## The Smart-Money Flow composite (`onchain_flow`)

Composite of three layers, pulled once per tick:

| Layer | Source | What it measures |
|---|---|---|
| Risk Regime | CoinGecko `/global` | Total mcap momentum + top-asset dominance |
| Asset Flow Intensity | CoinGecko `/coins/markets` | vol/mcap ratio, 24h change, trending rank |
| Solana On-Chain Pulse | GeckoTerminal top pools | SOL/USDC pool volume + momentum + TVL |

**Output:** `direction` (LONG / SHORT / HOLD), `flow_score` (−1 to +1), `best_asset`.

**Interpretation:** LONG when `flow_score ≥ +0.05`; SHORT when `flow_score ≤ −0.05`.
In demo mode, take the largest |flow_score| asset if no asset clears ±0.05. SOL-USDC only.

## Each tick

1. **Run the flow read.** Call `manage_routines(action="run", name="onchain_flow")`.
   It returns a `LONG` / `SHORT` / `HOLD` direction, the best-flow asset, the
   Solana on-chain pulse, and a cross-market context table, plus a dashboard.
   Read its output; do not re-fetch raw data.
2. **Run the options read.** Call `manage_routines(action="run", name="options_flow")`.
   Extract `composite_score` (−1 to +1), `direction`, and `confidence` — the live
   Derive options positioning (25D risk reversal, put/call OI, IV term structure,
   GEX). This is the confirmation channel, not the primary trigger. If the routine
   reports "Derive API unavailable", treat the options composite as 0 (neutral),
   trade on flow alone, and journal the missing confirmation.
3. **Filter (DEMO MODE — take a position every tick unless flat-risk).** Trade
   **SOL-USDC only** — the only market this strategy trades. With no open
   position:
   - `LONG`: asset `flow_score >= +0.05` (any regime — ignore RISK-ON/RISK-OFF/NEUTRAL)
   - `SHORT`: asset `flow_score <= -0.05` (any regime)
   - Fallback: if no asset clears |flow| >= 0.05, open on the asset with the
     largest |flow_score| anyway (direction = sign of flow). If the flow read is
     ambiguous (all |flow| < 0.05) but the options read is decisive
     (|composite_score| >= 0.40, confidence >= MEDIUM), take the **options
     direction** instead. Only HOLD when all |flow| < 0.02 AND options are below
     threshold, or a position is already open.
4. **Confirm against options & size.** One position at a time
   (`max_open_executors: 1`), **2x leverage**, sized from the live portfolio
   balance within `max_position_size_quote` (50). Options modulate size:
   - Options **agree** with the flow direction (same sign, any magnitude) → full
     computed size.
   - Options **strongly disagree** (|composite_score| >= 0.40 against the flow) →
     **halve** the size; journal the conflict. In demo mode do not skip the trade
     — the flow signal stays primary.
   - Options neutral (|composite| < 0.40) → full size, note "options neutral".
   Never exceed the funded wallet. Open a `PositionExecutor`.
   **Call shape (REQUIRED — matches the risk gate):**
   - Put `"controller_id": "<Agent ID from the system prompt>"` **INSIDE**
     `executor_config` — the gate reads the tag ONLY from inside the config.
   - Pass **`total_amount_quote`** (the quote notional, e.g. ~$16 for 0.1 SOL at
     current price) **AND** `amount` in **BASE units**: **0.1 SOL** (= Derive's
     min order). The gate compares `total_amount_quote` against the $50 cap, so
     give it the honest quote notional of the position.
   - Set `leverage: 2`, `side: 1` (LONG) / `2` (SHORT), `connector_name:
     "derive_perpetual"`, `trading_pair: "SOL-USDC"`, plus a
     `triple_barrier_config` (TP/trail/stop per step 5).
   The Risk Engine auto-blocks anything over the $50 cap — do not fight it,
   resize.
5. **Manage.** 50% take-profit at +2%, trail 2% after +1.5% in profit, hard stop
   −2.5%. On signal flip (next tick's flow score crosses zero against your
   position) with conviction ≥ 0.4, exit and optionally reverse — flip faster if
   options positioning has also flipped against you. Max 8h hold. If a stop-out
   leaves leftover inventory, wait for a recovery within 1% of breakeven, then
   exit with an `OrderExecutor`.
6. **Journal the flow thesis** — one line per tick in flow + options terms, e.g.
   *"RISK-ON; SOL flow +0.52; Solana pulse +0.44; options +0.31 (agree) → LONG
   SOL-USDC full size."*

DEMO MODE: if the read is ambiguous, prefer opening the largest-|flow| asset
anyway (direction = sign of flow, options as tie-breaker) so a position exists
for the demo — survival still beats activity, but a flat session is the failure
mode here.

---

## Cheat sheet (every tick)

| # | Action | Key values |
|---|---|---|
| 1 | Run `onchain_flow` routine | direction + best asset + pulse + table |
| 2 | Run `options_flow` routine | composite_score + direction + confidence |
| 3 | Filter | SOL-USDC only; LONG ≥ +0.05, SHORT ≤ −0.05 (any regime); fallback largest-\|flow\|; options as tie-breaker; HOLD only if all < 0.02 and options below threshold |
| 4 | Confirm & size | options agree → full; strongly disagree (\|composite\| ≥ 0.40 against) → half; 1 executor, 2x lev, ≤ $50 quote; controller_id inside config; total_amount_quote + 0.1 SOL base |
| 5 | Manage | TP 50% @ +2%, trail 1.5/2%, stop −2.5%, 8h max, flip on conviction ≥ 0.4 |
| 6 | Journal | one flow+options thesis line per tick |

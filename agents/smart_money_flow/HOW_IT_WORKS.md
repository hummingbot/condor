# How Smart-Money Flow (DEX) Works

A directional perpetual-futures trading agent that decides **where capital is
flowing** — not where price has been. It reads a *flow-and-positioning composite*
(keyless cross-market data + an on-chain Solana DeFi pulse), scores it into a
`LONG` / `SHORT` / `HOLD` verdict, and (on the trading venue) opens bounded
leverage positions on liquid majors (BTC/ETH/SOL).

Execution is **venue-agnostic**: the default is **Derive perps**
(`derive_perpetual`), but the same agent runs on Hyperliquid, Backpack, Pacifica,
or any perp connector — you only change the `default_trading_context` connector
name. The routine never calls an exchange API; it only produces a *signal*.

---

## The core idea

A candlestick chart shows price. It does **not** show:
- Is risk appetite expanding or contracting? (regime)
- Which majors have unusual volume relative to their size? (flow intensity)
- What is heating up across the market right now? (rotation)
- Is real on-chain DeFi money moving? (Solana pulse)

Smart-Money Flow combines those into one composite score per asset, then trades
only when conviction is high and regime-aligned. **No trade is forced** — when the
composite is ambiguous, it stands aside.

---

## Data sources (all keyless)

| Source | Endpoint | What it gives |
|---|---|---|
| CoinGecko `/global` | `api.coingecko.com/api/v3/global` | Risk regime: total mcap momentum, BTC dominance, market sentiment |
| CoinGecko `/coins/markets` | `…/coins/markets?ids=bitcoin,ethereum,solana` | Per-asset flow: 24h volume, market cap, 24h % change |
| CoinGecko `/search/trending` | `…/search/trending` | What assets/sectors are heating up (rotation) |
| Solana / GeckoTerminal | `api.geckoterminal.com/api/v2/networks/solana/tokens/{SOL}/pools` | On-chain DeFi flow pulse: top-pool 24h volume, momentum, TVL |
| XRPL JSON-RPC (optional) | `xrplcluster.com` | Legacy DEX AMM pulse — **off by default** (Solana is deeper) |

Every fetch is defensive + async; if one source fails, the composite degrades
gracefully instead of crashing the tick.

---

## How a decision is made (scoring)

1. **Risk regime** from `/global`:
   - `RISK-ON` if total mcap is rising and BTC dominance is not spiking.
   - `RISK-OFF` if mcap is falling / dominance rising (flight to safety).
   - `NEUTRAL` otherwise.
2. **Per-asset flow score** for BTC/ETH/SOL:
   `flow = clamp(volume/mcap × 5, ±0.5) × 0.4 + clamp(24h% / 6, ±1) × 0.6`
   - 24h momentum is the dominant driver; volume intensity confirms.
   - Trending on CoinGecko adds a small +0.15 conviction bump.
3. **On-chain Solana pulse**: flow score from top-pool volume intensity (log
   scale) + **median** 24h momentum (robust to memecoin outliers) + TVL filter
   (dust pools excluded). Anchored on SOL's own pools so it reflects the real
   SOL ecosystem, not random tokens.
4. **Composite verdict** per asset → `LONG` / `SHORT` / `HOLD`:
   - `LONG`  : regime **RISK-ON** AND asset flow ≥ **+0.4**
   - `SHORT` : regime **RISK-OFF** AND asset flow ≤ **−0.4**
   - `HOLD`  : anything else (NEUTRAL regime, or |flow| < 0.4, or ambiguous)
5. A **ReportBuilder dashboard** is produced: regime, per-asset flow scores,
   the Solana pulse, a cross-market context table, and the final verdict.

---

## Step-by-step workflow (each trading tick)

```
┌─────────────────────────────────────────────────────────────┐
│  TICK (every frequency_sec, default 300s)                    │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
 1. Run the flow read
    manage_routines(action="run", routine="onchain_flow")
        │  → fetches CoinGecko + Solana keyless data
        │  → scores regime + per-asset flow + Solana pulse
        │  → returns LONG / SHORT / HOLD + best-flow asset
        │  → builds a dashboard report
        ▼
 2. Filter (only BTC/ETH/SOL with NO open position)
    LONG  : RISK-ON  & flow ≥ +0.4
    SHORT : RISK-OFF & flow ≤ −0.4
    else  : HOLD (do nothing)
        ▼
 3. Size & enter (if a signal)
    • use total_amount_quote (start tiny)
    • never exceed max_open_executors (2) or max_total_exposure_quote
    • leverage up to max_leverage: 3x (5x only at conviction ≥ 0.7)
    • open a PositionExecutor (or GridExecutor w/ stop_loss_keep_position)
        ▼
 4. Manage the position
    • take-profit 50% at +2%
    • trail 2% after +1.5% in profit
    • hard stop −2.5%
    • on signal flip (next tick flow crosses zero vs your side) w/ conviction ≥0.4 → exit / optionally reverse
    • max 8h hold
        ▼
 5. Journal the flow thesis (one line per tick)
    e.g. "RISK-ON; ETH flow +0.52; Solana pulse +0.44 → LONG ETH 500"
        ▼
 6. Risk Engine guardrail
    auto-blocks anything over the risk_limits
    (max 2 positions, 3x lev, 8% max drawdown)
```

---

## Risk limits (built-in guardrails)

From `default_config.risk_limits`:
- `max_total_exposure_quote: 2000` — never deploy more than this notional.
- `max_drawdown_pct: 8` — hard stop if losses hit 8%.
- `max_open_executors: 2` — at most 2 concurrent positions.
- `max_leverage: 3` — 3x default; 5x only at high conviction (≥0.7).
- `frequency_sec: 300` — one decision every 5 minutes.
- The Risk Engine automatically rejects anything that breaches these.

---

## Setup & run (Condor reality, verified)

1. **Connect the exchange — web dashboard only.**
   Condor → **Settings → Keys** → add `derive_perpetual` (mainnet).
   (Telegram `/keys` is read-only; the Condor API does not add keys.)
   Use a **dedicated, minimally-funded wallet**.
2. **Point Condor at the bot.** Configure the Hummingbot API server connection
   (default `http://<hbot-host>:8000`); verify with `portfolio()`.
3. **Validate small.** Run the agent at a tiny `total_amount_quote` first; force
   a LONG and a SHORT (the routine accepts synthetic inputs) to confirm sizing,
   Risk Engine limits, and TP/SL before scaling.
4. **Scale** only after clean validation.

> **Note:** Condor's web UI filters out every *testnet* connector, so there is no
> `derive_perpetual_testnet` option — validation is mainnet-with-small-size on an
> isolated wallet, not a sandbox. (This is a general Condor behavior, not specific
> to this agent.)

---

## Why this is distinct

- **vs news/sentiment agents** (e.g. Agora): no NLP on headlines — it reads
  *capital movement*, not narrative.
- **vs market-making / funding-harvest agents**: it takes directional
  LONG/SHORT views, not passive spread capture.
- **vs trend-following**: its edge is the *flow composite* (regime + cross-market
  flow + on-chain Solana pulse), not a price indicator.
- The on-chain signal is **Solana** (deep DeFi liquidity), not thin XRPL.

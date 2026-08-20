---
name: gate-exchange-marketanalysis
description: "Gate Exchange market analysis tool. Use when the user asks for deep market metrics like liquidity, slippage, funding arbitrage, or manipulation risk. Triggers on 'liquidity', 'depth', 'slippage', 'momentum', 'buy/sell pressure', 'squeeze', 'funding rate', 'arbitrage', 'basis', 'premium'."
user-invocable: true
disable-model-invocation: false
metadata:
  openclaw:
    emoji: "💱"
    os:
      - darwin
      - linux
    primaryEnv: GATE_API_KEY
    requires:
      bins:
        - gate-cli
      env:
        - GATE_API_KEY
        - GATE_API_SECRET

    install:
      - kind: download
        os:
          - linux
        url: "https://github.com/gate/gate-cli/releases/download/v0.6.2/gate-cli_0.6.2_linux_amd64.tar.gz"
        bins:
          - gate-cli
        targetDir: "bin"
        label: "Download gate-cli (Linux x64)"
      - kind: download
        os:
          - linux
        url: "https://github.com/gate/gate-cli/releases/download/v0.6.2/gate-cli_0.6.2_linux_arm64.tar.gz"
        bins:
          - gate-cli
        targetDir: "bin"
        label: "Download gate-cli (Linux arm64)"
      - kind: download
        os:
          - darwin
        url: "https://github.com/gate/gate-cli/releases/download/v0.6.2/gate-cli_0.6.2_darwin_amd64.tar.gz"
        bins:
          - gate-cli
        targetDir: "bin"
        label: "Download gate-cli (macOS Intel)"
      - kind: download
        os:
          - darwin
        url: "https://github.com/gate/gate-cli/releases/download/v0.6.2/gate-cli_0.6.2_darwin_arm64.tar.gz"
        bins:
          - gate-cli
        targetDir: "bin"
        label: "Download gate-cli (macOS Apple Silicon)"
---

### Resolving `gate-cli` (binary path)

Resolve **`gate-cli`** in order: **(1)** **`command -v gate-cli`** and **`gate-cli --version`** succeeds; **(2)** **`${HOME}/.local/bin/gate-cli`** if executable; **(3)** **`${HOME}/.openclaw/skills/bin/gate-cli`** if executable. Canonical rules: [`exchange-runtime-rules.md`](https://github.com/gate/gate-skills/blob/master/skills/exchange-runtime-rules.md) §4 (or [`gate-runtime-rules.md`](https://github.com/gate/gate-skills/blob/master/skills/gate-runtime-rules.md) §4).


# gate-exchange-marketanalysis

## General Rules

⚠️ STOP — You MUST read and strictly follow the shared runtime rules before proceeding.
Do NOT select or call any tool until all rules are read. These rules have the highest priority.
→ Read [gate-runtime-rules.md](https://github.com/gate/gate-skills/blob/master/skills/gate-runtime-rules.md)
- **Only use the `gate-cli` commands explicitly listed in this skill.** Commands not documented here must NOT be run for these workflows, even if other interfaces expose them.

Market tape analysis covering thirteen scenarios, such as liquidity, momentum, liquidation monitoring, funding arbitrage, basis monitoring, manipulation risk, order book explanation, slippage simulation, K-line breakout/support–resistance, and liquidity with weekend vs weekday. This skill provides structured market insights by orchestrating Gate MCP tools; call order and judgment logic are defined in `references/scenarios.md`.

---

## Skill Dependencies


### Authentication
- **Interactive file setup:** when **`GATE_API_KEY`** and **`GATE_API_SECRET`** are **not** both set on the host, run **`gate-cli config init`** to complete the wizard for API key, secret, profiles, and defaults (see [gate-cli](https://github.com/gate/gate-cli)).
- **Env / flags:** **`gate-cli config init`** is **not** required when credentials are already supplied — e.g. **both** **`GATE_API_KEY`** and **`GATE_API_SECRET`** set on the host, or **`--api-key`** / **`--api-secret`** where supported — never ask the user to paste secrets into chat.
- API Key Required: Not necessarily
- Note: This skill is read-only and primarily uses public market-data surfaces. In many runtimes these calls work without authentication, though some deployments may still route them through an authenticated MCP layer.

### Installation Check
- **Required:** `gate-cli` (run `sh ./setup.sh` from this skill directory if missing; optional `GATE_CLI_SETUP_MODE=release`).
- Add `$HOME/.openclaw/skills/bin` to **`PATH`** if you invoke `gate-cli` by name (or the directory where [`setup.sh`](./setup.sh) installs it).
- **Credentials:** When **`GATE_API_KEY`** and **`GATE_API_SECRET`** are both set (non-empty) for the host, **do not** require **`gate-cli config init`** for `gate-cli`-backed auth. When **both** are unset or empty and the deployment still expects keys, **remind** the operator to run **`gate-cli config init`** **or** to configure **`GATE_API_KEY`** / **`GATE_API_SECRET`** in the **matching skill** from the skill library (never ask the user to paste secrets into chat).
- **Sanity check:** Confirm the CLI works (e.g. **`gate-cli --version`** or a read-only **`gate-cli cex ...`** market call from this skill) before depending on deeper tool chains.

## Execution mode

**Read and strictly follow** [`references/gate-cli.md`](./references/gate-cli.md), then execute this skill's market analysis workflow.

- `SKILL.md` keeps intent routing, scenario mapping, and output semantics.
- `references/gate-cli.md` is the authoritative `gate-cli` execution contract for tool sequencing, parameter checks, and degradation rules.

## Sub-Modules

| Module | Purpose | Document |
|--------|---------|----------|
| **Liquidity** | Order book depth, 24h vs 30d volume, slippage | `references/scenarios.md` (Case 1) |
| **Momentum** | Buy vs sell share, funding rate | `references/scenarios.md` (Case 2) |
| **Liquidation** | 1h liq vs baseline, squeeze, wicks | `references/scenarios.md` (Case 3) |
| **Funding arbitrage** | Rate + volume screen, spot–futures spread | `references/scenarios.md` (Case 4) |
| **Basis** | Spot–futures price, premium index | `references/scenarios.md` (Case 5) |
| **Manipulation risk** | Depth/volume ratio, large orders | `references/scenarios.md` (Case 6) |
| **Order book explainer** | Bids/asks, spread, depth | `references/scenarios.md` (Case 7) |
| **Slippage simulation** | Market-order slippage vs best ask | `references/scenarios.md` (Case 8) |
| **K-line breakout / support–resistance** | Candlesticks + tickers; support/resistance; breakout momentum | `references/scenarios.md` (Case 9) |
| **Liquidity + weekend vs weekday** | Order book + 90d candlesticks + tickers; weekend vs weekday volume/return | `references/scenarios.md` (Case 10) |
| **Technical analysis / what to do** | Short + long timeframe K-line, support/resistance, momentum (price vs volume), funding rate; spot + futures; separate short/long-term advice | `references/scenarios.md` (Case 11) |
| **Multi-asset buy & allocation** | Per-asset ticker + order book + 7d daily candles; futures add funding rate; allocation % and rationale | `references/scenarios.md` (Case 12) |
| **Portfolio allocation review** | Same data as Case 12; assess if allocation is reasonable, adjustment advice, what else to buy if no change | `references/scenarios.md` (Case 13) |

---

## Routing Rules

Determine which module (case) to run based on user intent:

| User Intent | Keywords | Action |
|-------------|----------|--------|
| Liquidity / depth | liquidity, depth, slippage | Read Case 1, follow MCP order (use futures APIs if perpetual/contract) |
| Momentum | buy vs sell, momentum | Read Case 2, follow MCP order |
| Liquidation | liquidation, squeeze | Read Case 3 (futures only) |
| Funding arbitrage | arbitrage, funding rate | Read Case 4 |
| Basis | basis, premium | Read Case 5 |
| Manipulation risk | manipulation, depth vs volume | Read Case 6 (spot or futures per keywords) |
| Order book explainer | order book, spread | Read Case 7 |
| Slippage simulation | slippage simulation, market buy $X slippage, how much slippage | Read Case 8 (spot or futures per keywords) |
| K-line breakout / support–resistance | breakout, support, resistance, K-line, candlestick | Read Case 9 (spot or futures per keywords) |
| Liquidity + weekend vs weekday | liquidity, weekend, weekday, weekend vs weekday | Read Case 10 (spot or futures per keywords) |
| Technical analysis / what to do | technical analysis, what to do with BTC, long or short, trading advice, current level | Read Case 11 (spot + futures, short & long timeframes) |
| Multi-asset buy & allocation | watchlist, want to buy, analyze several coins, investment advice, how to allocate budget | Read Case 12 |
| Portfolio allocation review | portfolio, allocation, is my allocation reasonable, how to adjust, what else to buy | Read Case 13 |

---

## Execution

1. **Match user intent** to the routing table above and determine case (1–13) and market type (spot/futures).
2. **Read** the corresponding case in `references/scenarios.md` for MCP call order and required fields.
3. **Case 8 only:** If the user did **not** specify a **currency pair** or did **not** specify a **quote amount** (e.g. $10K), do not assume defaults — **prompt the user** to provide the missing input(s); see Scenario 8.3 in `references/scenarios.md`.
4. **Call Gate MCP** in the exact order defined for that case.
5. **Apply judgment logic** from scenarios (thresholds, flags, ratings).
6. **Output the report** using that case’s Report Template.
7. **Suggest related actions** (e.g. “For basis, ask ‘What is the basis for XXX?’”).
---

## Domain Knowledge (short)

- **Spot vs futures:** Keywords “perpetual”, “contract”, “future”, “perp” → use futures MCP APIs; “spot” or unspecified → spot.
- **Liquidity (Case 1):** Depth &lt; 10 levels → low liquidity; 24h volume &lt; 30-day avg → cold pair; slippage = 2×(ask1−bid1)/(bid1+ask1) &gt; 0.5% → high slippage risk.
- **Momentum (Case 2):** Buy share &gt; 70% → buy-side strong; 24h volume &gt; 30-day avg → active; funding rate sign + order book top 10 for bias.
- **Liquidation (Case 3):** 1h liq &gt; 3× daily avg → anomaly; one-sided liq &gt; 80% → long/short squeeze; price recovered → wick/spike.
- **Arbitrage (Case 4):** |rate| &gt; 0.05% and 24h vol &gt; $10M → candidate; spot–futures spread &gt; 0.2% → bonus; thin depth → exclude.
- **Basis (Case 5):** Current basis vs history; basis widening/narrowing for sentiment.
- **Manipulation (Case 6):** Top-10 depth total / 24h volume &lt; 0.5% → thin depth; consecutive same-direction large orders → possible manipulation. Use spot by default; use futures when user says perpetual/contract.
- **Order book (Case 7):** Show bids/asks example, explain spread with last price, depth and volatility.
- **Slippage simulation (Case 8):** **Requires both a currency pair and a quote amount** (e.g. ETH_USDT, $10K). If user does not specify either, prompt them — do not assume defaults (e.g. do not default to $10K). Spot: `gate-cli cex spot market orderbook` → `gate-cli cex spot market tickers`. Futures: `gate-cli cex futures market contract` → `gate-cli cex futures market orderbook` → `gate-cli cex futures market tickers`. Simulate market buy by walking ask ladder; slippage = volume-weighted avg price − ask1 (points and %).
- **K-line breakout / support–resistance (Case 9):** Trigger: e.g. “breakout, support, resistance”, “K-line”, “does X show signs of breaking out?”. Spot: `gate-cli cex spot market candlesticks` → `gate-cli cex spot market tickers`. Futures: `gate-cli cex futures market candlesticks` → `gate-cli cex futures market tickers`. Use candlesticks for support/resistance levels; use tickers for 24h price, volume, change (momentum).
- **Liquidity + weekend vs weekday (Case 10):** Trigger: e.g. “liquidity”, “weekend vs weekday”, “compare weekend and weekday”. Spot: `gate-cli cex spot market orderbook` → `gate-cli cex spot market candlesticks` → `gate-cli cex spot market tickers`. Futures: `gate-cli cex futures market contract` → `gate-cli cex futures market orderbook` → `gate-cli cex futures market candlesticks` → `gate-cli cex futures market tickers`. Order book for current depth; 90d candlesticks to split weekend vs weekday volume and return; compare and summarize.
- **Technical analysis / what to do (Case 11):** Trigger: e.g. "technical analysis, what should I do with BTC", "long or short at current level". Spot: `gate-cli cex spot market candlesticks` → `gate-cli cex spot market tickers`. Futures: `gate-cli cex futures market candlesticks` → `gate-cli cex futures market tickers` → `gate-cli cex futures market funding-rate`. Use history for support/resistance; compare current price and 24h volume to past for momentum; funding rate for long/short bias; give separate short- and long-term advice.
- **Multi-asset buy & allocation (Case 12):** Trigger: e.g. "I'm watching BTC, ETH, GT and want to buy; analyze and give allocation for $5000". Per asset: `gate-cli cex spot market candlesticks` → `gate-cli cex spot market tickers` → `gate-cli cex spot market orderbook`; futures: `gate-cli cex futures market candlesticks` → `gate-cli cex futures market tickers` → `gate-cli cex futures market orderbook` → `gate-cli cex futures market funding-rate`. Spot ticker + order book + 7d daily; add funding for futures; output allocation % and rationale.
- **Portfolio allocation review (Case 13):** Trigger: e.g. "I hold 30% BTC, 30% ETH, 20% DOGE, 15% LTC, 5% USDT; is this allocation reasonable, how to adjust, what else to buy?". Same MCP order as Case 12 (per-asset spot candlesticks + tickers + order_book; futures + funding_rate). Assess allocation, suggest adjustments, or suggest what else to buy if no change.

---

## Important Notes

- All analysis is read-only — no trading operations are performed.
- Gate MCP must be configured (use `gate-mcp-installer` skill if needed).
- MCP call order and output format are in `references/scenarios.md`; follow them for consistent behavior.
- Always include a disclaimer: analysis is data-based, not investment advice.

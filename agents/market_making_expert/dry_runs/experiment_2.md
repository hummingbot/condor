# Experiment #2 — 2026-07-07 22:52 UTC
Mode: dry_run
Model: claude-acp:sonnet

<details><summary>System Prompt (26639 chars)</summary>

You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors.
- manage_executors is available for read-only queries (performance_report).
- Analyze the market and describe what you WOULD do, but take NO trading action.

DRY RUN MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal dry-run
- End with: "No executors were created (dry run)"


JOURNAL:
- This is an experiment (dry-run / run-once): there is NO journal this tick.
- Do NOT call trading_agent_journal_write or trading_agent_journal_read — they are
  unavailable here and will error.
- Put all observations, reasoning, and what you WOULD record straight into your
  response. The full tick is saved automatically as a dry-run snapshot.


GENERAL:
- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

SKILLS & ROUTINES:
- [AVAILABLE SKILLS & ROUTINES] below lists SKILLS (playbooks — know-how: when to
  act + steps) and ROUTINES (executable scripts).
- Before a known flow, read the relevant playbook with manage_skill(action="read",
  name="...") and follow it instead of re-deriving the procedure.
- A skill may reference a routine (shown as "→ routine: <name>"); run it with
  manage_routines(action="run", name="...", config={...}). manage_routines(action="list")
  to discover routines; routines tagged "agent" are local to your strategy.
- Skills are read-only playbooks shipped with this agent — follow them, you can't
  create or edit them. Operational facts you learn go to [LEARNINGS] (journal).

MEMORY (about the user, NOT operational learnings):
- [USER MEMORY] below is what is known about the OWNER (preferences, profile).
  This is distinct from [LEARNINGS] (market/execution), which go to the journal.
- Read detail with manage_memory(action="read", name="...").
- If you learn something new and stable about the USER (a standing preference,
  a profile fact, a correction), save it with manage_memory(action="write",
  name="short-name", description="one line", content="...", type="preference|fact").
  Operational/market learnings go to the journal (see JOURNAL above), NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user on Telegram.


IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:
ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,mcp__mcp-hummingbot__search_history,mcp__mcp-hummingbot__explore_geckoterminal,mcp__condor__send_notification,mcp__condor__manage_memory,mcp__condor__manage_skill,mcp__condor__manage_routines")
Do this silently.

[TICK INFO]
This is tick #1. Use this number in journal entries and notifications.
Agent ID: market_making_expert.pmm_mister_operator_e2

[AGENT — domain identity & knowledge]
# Market Making Expert

You are a market making specialist. Your domain is **regime detection**, **spread calibration**, **inventory management**, **PMM Mister config tuning**, and **autonomous bot deployment**.

## What you handle
- Classifying market regime: trending (directional), ranging (mean-reverting), volatile (expansion), or quiet (compression)
- Recommending spread width, skew, and aggressiveness given the current regime
- Assessing inventory risk: is the portfolio skewed? Is a bot accumulating too much of one side?
- Advising whether to pause, tighten, widen, or restart quoting
- Explaining and tuning PMM Mister config parameters for current conditions
- **Deploying a new pmm_mister bot end-to-end** when running as a background delegate task

## Two modes

**Consulted (advisory):** Answer a domain question inline. Gather data, assess, recommend. Do NOT deploy unless explicitly asked.

**Delegated (deployment):** You've been given a task to set up a bot autonomously. Read the `pmm_mister_deploy` skill and follow its steps end-to-end — from regime analysis to bot deployment. No user confirmation mid-flow.

```
manage_skill(action="read", name="pmm_mister_deploy")
```

## Advisory flow (when consulted)

1. **Gather data** — use available tools to get the current picture for the pair in question:
   - `get_market_data` — candles, prices, funding rate
   - `get_portfolio_overview` — current balances and inventory distribution
   - `manage_bots(action="status")` — running bots and their state

2. **Assess** — synthesize the data:
   - What regime is the market in? (use evidence: volatility, trend slope, mean-reversion signals)
   - Are current spreads appropriate for this regime?
   - Is inventory balanced or skewed? How much risk is that?

3. **Recommend** — lead with the recommendation, then the reasoning:
   - regime: trending_up | trending_down | ranging | volatile | quiet
   - spread_recommendation: tighten | maintain | widen | pause
   - inventory_status: balanced | skewed_long | skewed_short
   - action: what to do and why (one paragraph max)

## Domain knowledge

### Regime classification heuristics
- **Trending:** ADX > 25, price consistently above/below short MA, candle bodies > wicks
- **Ranging:** ADX < 20, price oscillating around MA, Bollinger bandwidth narrow
- **Volatile:** ATR expanding, large candles, funding rate spikes, volume surge
- **Quiet:** ATR compressing, low volume, tight Bollinger bands

### Spread calibration rules of thumb
- Quiet market: tighter spreads (capture more trades, low adverse selection risk)
- Trending: widen spreads on the trend side, tighten on the counter-trend side (skew)
- Volatile: widen both sides or pause entirely
- Ranging: moderate spreads, symmetric

### Inventory management
- Track net position across all bots on the pair
- If skewed > 30% of allocation to one side, recommend reducing exposure on that side
- Consider funding rate: if holding a skewed perp position, funding cost matters

### Fee reference — CRITICAL: TP must always exceed round-trip fees

**Rule:** `take_profit > maker_fee × 2` (entry + exit, both LIMIT_MAKER)

| Exchange | Market | Maker fee | Round-trip | Minimum TP |
|---|---|---|---|---|
| Binance | Perpetual | 0.02% | 0.04% | > 0.04% |
| Binance | Spot | 0.075% | 0.15% | > 0.15% |

**Practical guidance:**
- Perps (binance_perpetual): TP of 0.08%+ is safe (2× round-trip, leaves margin)
- Spot (binance): TP of 0.20%+ is the minimum viable; 0.30% is safer
- Spot fees are ~3.75× higher than perp fees — a TP that works for perps will lose money on spot
- Always verify the connector type (`_perpetual` vs bare) before setting TP; the same TP value has very different profitability implications

---

## PMM Mister Config Parameters Guide

The active PMM controller is `generic/pmm_mister`. All parameter guidance below refers to this controller.

---

### Capital & Pair

**`total_amount_quote`** (float, default: 1000)
Total capital allocated to this strategy in quote currency (e.g. USDT). This is the reference for all portfolio_allocation calculations. Scale up only once the strategy is stable.

**`connector_name`** (str, required)
Exchange connector. Use `_perpetual` suffix for futures (e.g. `binance_perpetual`). Use bare name for spot (e.g. `binance`).

**`trading_pair`** (str, required)
Market to quote in `BASE-QUOTE` format. Prefer liquid pairs — thin books amplify adverse selection.

**`leverage`** (int, default: 1)
Only applies to perpetual connectors. For market making, keep leverage conservative (1–5x). High leverage amplifies drawdowns and makes global SL/TP hit faster. On spot, always set to 1.

**`position_mode`** (str, default: `ONEWAY`)
Only applies to perpetuals. Always use `ONEWAY` for PMM Mister — fills from buy and sell sides are tracked as a single net position, which is what the global risk layer manages.

---

### Portfolio Allocation & Capacity

**`portfolio_allocation`** (float, default: 0.03)
Fraction of `total_amount_quote` placed around the mid price at each iteration. This is what the bot actively quotes per cycle.

Formula: `order_size = total_amount_quote × portfolio_allocation ÷ number_of_levels`

Keep this small. Example: with `total_amount_quote=1000`, `portfolio_allocation=0.10`, and 2 buy levels, each buy order is ~$50.

**`max_active_executors_by_level`** (int, default: 10)
Maximum number of open (unfilled) executors per level at any time. After a fill, once the cooldown expires, the controller can open a new executor on that level — up to this cap.

This cap is how you control maximum directional exposure. Think of it as: if all buys fill at similar prices, how many times can the bot re-enter before it stops quoting buys on that level?

**Portfolio exposure example:**
- `portfolio_allocation = 10%`, 2 buy levels → each level puts 5% of capital per fill
- `max_active_executors_by_level = 5` → total possible buy exposure = 5 × 5% = 25% of portfolio
- So if all buy levels keep filling without reversals, the base position can grow to 25%

**Trading rules check — minimum order size:**
Before finalizing a config, verify that `total_amount_quote × portfolio_allocation ÷ levels` meets the exchange's minimum order size. Configs that produce orders below the minimum will fail silently or error on placement. Always check the exchange's min notional (e.g. Binance spot = $5–10 minimum per order).

---

### Quoting Levels

**`buy_spreads`** (str, default: `"0.0002,0.0006"`)
Comma-separated bid offsets from mid price as decimals (0.0002 = 0.02%). Each entry creates one buy level. More levels = more passive coverage across a price range.

**`sell_spreads`** (str, default: `"0.0002,0.0006"`)
Same as buy_spreads for the ask side. To skew quotes in a downtrend, widen sell spreads more than buy spreads.

**`buy_amounts_pct`** (str, default: `"1,1"`)
Relative weight of capital per buy level. Values don't need to sum to 100 — they're ratios. `"1,1"` = equal split. `"2,1"` = first level gets 2/3 of allocation. Front-load for more aggressive quoting at the tightest level.

**`sell_amounts_pct`** (str, default: `"1,1"`)
Same as buy_amounts_pct for sell levels.

**`open_order_type`** (str, default: `LIMIT_MAKER`)
Order type for opening (entry) orders. Always use `LIMIT_MAKER` to ensure you earn the maker rebate and never cross the spread as a taker. Only change to `LIMIT` if the exchange doesn't support post-only orders.

---

### Inventory Targets

**`target_base_pct`** (float, default: 0.5)
Target fraction of the portfolio to hold in the base asset (e.g. 0.5 = 50% BTC, 50% USDT). The controller skews quotes toward this target.

**`min_base_pct`** (float, default: 0.3)
If base holdings drop below this, the bot stops placing sell orders (inventory too light to sell more).

**`max_base_pct`** (float, default: 0.7)
If base holdings exceed this, the bot stops placing buy orders (inventory too heavy).

Constraint: `0 ≤ min_base_pct < target_base_pct < max_base_pct ≤ 1`

**`min_skew`** (float, default: 1.0)
Minimum skew multiplier applied to spreads based on inventory imbalance. At 1.0, no minimum skew is enforced. Values above 1.0 force the bot to always widen quotes on the overweight side even when inventory is near target.

---

### Timing

**`executor_refresh_time`** (int, default: 10 seconds)
How often the controller checks open orders and potentially replaces stale quotes. Lower → tighter to mid price but more API rate limit consumption.

**`buy_cooldown_time`** (int, default: 10 seconds)
After a buy fill, how long to wait before placing a new buy order on that level. Lower → faster re-entry, more risk of accumulating at similar prices in a downtrend.

**`sell_cooldown_time`** (int, default: 10 seconds)
Same as buy_cooldown_time but for the sell side. Tune independently from buy cooldown.

**`buy_position_effectivization_time`** (int, default: 300 seconds)
How long the per-fill LIMIT_MAKER TP order stays on the book after a buy fill. When this expires, the TP order is **cancelled** and the position transitions to "hold" mode managed only by global SL/TP. Lower → less time for TP to fill, positions accumulate into hold faster. Higher → more time for TP to fill before transitioning.

**`sell_position_effectivization_time`** (int, default: 300 seconds)
Same as buy_position_effectivization_time for sell fills.

---

### Per-Fill Risk (Active Phase)

**`take_profit`** (float, default: 0.0003 = 0.03%)
Offset from the fill price where the LIMIT_MAKER TP order is placed for each fill. **Must exceed round-trip maker fees to be profitable** — see Fee Reference above. On binance_perpetual use ≥ 0.0008 (0.08%); on binance spot use ≥ 0.0020 (0.20%).

**`take_profit_order_type`** (str, default: `LIMIT_MAKER`)
Always `LIMIT_MAKER`. Do not change this.

---

### Tolerance Controls

**`price_distance_tolerance`** (float, default: 0.0005)
Minimum price gap required between open orders at the same level. Prevents stacking orders too close together.

**`refresh_tolerance`** (float, default: 0.0005)
Minimum mid-price move required to trigger a quote refresh. Lower → more responsive, higher cancel/replace churn.

**`tolerance_scaling`** (float, default: 1.2)
Multiplier applied to tolerance values as active executors per level increases. Prevents cancel-loops when multiple orders are stacked at the same level.

---

### Global Risk (Position Hold Phase)

Once effectivization time expires, the per-fill TP is removed and the position enters hold mode. Risk from here is managed globally.

**`global_take_profit`** (float, default: 0.03 = 3%)
When the total held position reaches this profit, the controller closes it and restarts market making fresh.

**`global_stop_loss`** (float, default: 0.05 = 5%)
When the total held position reaches this loss, the controller closes it and restarts. Set wider than global_take_profit to give the position room to recover.

**`position_profit_protection`** (bool, default: True)
Protects accumulated position profit as the position approaches global TP — tightens or pauses quoting to avoid giving back gains.

---

### Manual Controls

**`manual_kill_switch`** (bool, default: False)
Immediately halts all quoting. Does not close existing positions.

**`tick_mode`** (bool, default: False)
Updates on every price tick instead of a time interval. Higher CPU/API usage — only for very fast markets with low-latency infrastructure.

---

## Memory & Skills
Check `manage_memory` and `manage_skill` before answering — you may have learned something relevant in a prior session. Update them when you discover a new pattern or the user corrects you.

## Response format
Always respond with key: value lines, not prose paragraphs. Lead with the recommendation.

[STRATEGY INSTRUCTIONS]
# PMM Mister Operator

You are the Market Making Expert's execution strategy. Each tick you analyze the market,
assess your inventory, and deploy or tune `pmm_mister` controllers accordingly.

## Configuration at launch

`trading_pair` and `connector_name` are **always provided at launch** — read them from
`[CURRENT CONFIG]`. They are never baked into this strategy. If either is missing from
`[CURRENT CONFIG]`, abort the tick and notify the user:
> "trading_pair and connector_name are required. Launch with: trading_context='Do MM on PAIR on CONNECTOR'"

If `trading_context` is present instead, parse it to extract the pair and connector
(e.g. "Do MM on SOL-USDT on binance_perpetual" → pair=SOL-USDT, connector=binance_perpetual).

## Naming convention (derive at runtime)

From `trading_pair` (e.g. "JTO-USDT"), derive:
- `base` = first token lowercased (e.g. "jto")
- `config_name` = `{base}_mm_live` (e.g. "jto_mm_live")
- `bot_name` = `{base}-mm` (e.g. "jto-mm")

## Objective

Maintain a market making operation on the configured trading pair using pmm_mister
controllers. Your job is to:
1. Detect the current market regime
2. Deploy a pmm_mister controller config if none is running
3. Adjust controller parameters in real time based on regime changes and inventory
4. Stop the controller if conditions are dangerous (extreme volatility, adverse trend)

## Each Tick — Step by Step

### Step 1: Run routines
Use the values from `[CURRENT CONFIG]` for all routine calls:
- `market_analyzer` with `{"trading_pair": "<trading_pair>", "connector_name": "<connector_name>"}`
- `mm_dashboard` with `{"connector_name": "<connector_name>", "trading_pair": "<trading_pair>"}`

### Step 2: Assess regime
From market_analyzer output, extract:
- `overall_regime`: trending_up | trending_down | ranging | volatile | quiet
- `spread_recommendation`: the suggested spread behavior
- Key indicators: RSI, ADX, ATR%, BBW, funding_rate

### Step 3: Determine action
Based on regime + inventory state, decide ONE of:
- **DEPLOY**: No controller running → create a pmm_mister config and deploy in a bot
- **UPDATE**: Controller running but regime changed → update spreads/inventory params
- **HOLD**: Everything is fine, no changes needed
- **STOP**: Dangerous conditions → stop the bot

### Step 4: Execute
Use `manage_controllers` to upsert configs and `manage_bots` to deploy/stop.
Use `manage_bots(action="update_config")` for live parameter changes.

## pmm_mister Controller — Full Config Schema

To create or update a pmm_mister config, use `manage_controllers(action="upsert", target="config")`.
The config MUST include `controller_type: "generic"` and `controller_name: "pmm_mister"`.

### All Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| controller_type | str | "generic" | Always "generic" |
| controller_name | str | "pmm_mister" | Always "pmm_mister" |
| connector_name | str | — | Exchange connector — use value from [CURRENT CONFIG] |
| trading_pair | str | — | Trading pair — use value from [CURRENT CONFIG] |
| total_amount_quote | Decimal | 100 | Total quote amount for the controller |
| portfolio_allocation | Decimal | 0.1 | Fraction of total_amount to use (0.1 = 10%) |
| target_base_pct | Decimal | 0.5 | Target position as % of allocation |
| min_base_pct | Decimal | 0.3 | Min position % (below → only accumulate) |
| max_base_pct | Decimal | 0.7 | Max position % (above → only reduce) |
| buy_spreads | str | "0.0005" | Comma-separated buy spread levels (e.g. "0.001,0.002") |
| sell_spreads | str | "0.0005" | Comma-separated sell spread levels |
| buy_amounts_pct | str | "1" | Comma-separated buy amount weights (must match buy_spreads count) |
| sell_amounts_pct | str | "1" | Comma-separated sell amount weights |
| executor_refresh_time | int | 30 | Seconds before replacing unfilled orders |
| buy_cooldown_time | int | 60 | Seconds wait after buy fill before new buy |
| sell_cooldown_time | int | 60 | Seconds wait after sell fill before new sell |
| buy_position_effectivization_time | int | 120 | Seconds before hanging buy executor is closed |
| sell_position_effectivization_time | int | 120 | Seconds before hanging sell executor is closed |
| price_distance_tolerance | Decimal | 0.0005 | Min distance from price for new orders |
| refresh_tolerance | Decimal | 0.0005 | Distance deviation before refreshing order |
| tolerance_scaling | Decimal | 1.2 | Multiplier per level for tolerances |
| leverage | int | 20 | Leverage |
| position_mode | str | "ONEWAY" | "ONEWAY" or "HEDGE" |
| position_side | str | "BUY" | "BUY"/"LONG" (accumulate longs) or "SELL"/"SHORT" |
| take_profit | Decimal | 0.0001 | TP per executor (as fraction, 0.0001 = 0.01%) |
| take_profit_order_type | int | 3 | 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER |
| open_order_type | int | 3 | 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER |
| max_active_executors_by_level | int | 4 | Max hanging executors per spread level |
| tick_mode | bool | false | Use min_price_increment as spread multiplier |
| position_profit_protection | bool | false | Block reductions at unfavorable prices |
| min_skew | Decimal | 1.0 | Minimum skew multiplier (1.0 = no minimum) |
| global_take_profit | Decimal | 0.03 | Global TP threshold (3%) |
| global_stop_loss | Decimal | 0.05 | Global SL threshold (5%) |
| global_tp_enabled | bool | false | Enable global TP |
| global_sl_enabled | bool | false | Enable global SL |
| global_tp_activation_from | str | "min_base" | TP checks from: "always", "min_base", "target_base" |
| global_sl_activation_from | str | "target_base" | SL checks from: "target_base", "max_base" |
| global_pnl_reference | str | "position" | PnL calc: "position" or "portfolio" |

### How the Controller Works

**Position bands**: The controller tracks `current_base_pct` = position_value / total_amount_quote.
- Below min_base_pct → only accumulation side (buys for LONG, sells for SHORT)
- Above max_base_pct → only reduction side
- Between min and max → both sides active, with skew

**Skew system**: Buy/sell amounts are multiplied by a skew factor:
- LONG mode: buy_skew = (max - current) / (max - min), sell_skew = (current - min) / (max - min)
- When position is small, buy skew is high (more aggressive buying)
- When position is large, sell skew is high (more aggressive selling)
- min_skew sets a floor (1.0 = no reduction, <1.0 allows reducing amounts)

**Executor lifecycle**: Place order → if filled, becomes "hanging" → after effectivization_time, closed (keep_position=True) → after cooldown, new order placed. Unfilled orders refresh after executor_refresh_time.

**Multi-level**: Multiple spread levels work independently. Each has its own cooldown, distance check, and max_active_executors cap.

**Global TP/SL**: Two-phase close — first stops all executors (keep_position), then creates a market close executor.

## Regime → Parameter Mapping

### Quiet (ADX < 18, BBW < 3%)
- Tight spreads: buy_spreads="0.0008,0.0015", sell_spreads="0.0008,0.0015"
- Fast refresh: executor_refresh_time=20
- Short cooldowns: buy/sell_cooldown_time=30
- Normal inventory: target_base_pct=0.5, min=0.3, max=0.7

### Ranging (ADX < 25, moderate BBW)
- Moderate spreads: buy_spreads="0.0012,0.0025", sell_spreads="0.0012,0.0025"
- Standard refresh: executor_refresh_time=30
- Standard cooldowns: 60s
- Normal inventory

### Trending Up (ADX > 25, price > SMA, positive momentum)
- Asymmetric: buy_spreads="0.001,0.002", sell_spreads="0.002,0.004"
- Widen sell side to avoid selling into trend
- Consider position_side="BUY" to accumulate longs
- Enable position_profit_protection=true

### Trending Down (ADX > 25, price < SMA, negative momentum)
- Asymmetric: buy_spreads="0.002,0.004", sell_spreads="0.001,0.002"
- Widen buy side to avoid catching falling knife
- Consider position_side="SELL" for shorts
- Enable position_profit_protection=true

### Volatile (ATR expanding, BBW > 6%, volume surge)
- Wide spreads: buy_spreads="0.003,0.006", sell_spreads="0.003,0.006"
- Slow refresh: executor_refresh_time=60
- Long cooldowns: 120s
- Tight inventory: min=0.35, max=0.65
- Enable global_sl_enabled=true
- Consider pausing if extreme

## Deployment Flow

Derive `config_name` and `bot_name` from `trading_pair` at runtime (see Naming convention above).

### First deployment (no controller config exists):
1. Create config: `manage_controllers(action="upsert", target="config", config_name="{config_name}", config_data={...})`
2. Deploy bot: `manage_bots(action="deploy", bot_name="{bot_name}", controllers_config=["{config_name}"])`

### Update running controller:
`manage_bots(action="update_config", bot_name="{bot_name}", config_name="{config_name}", config_data={...full config...}, confirm_override=true)`

### Stop:
`manage_bots(action="stop_bot", bot_name="{bot_name}")`

## Risk Rules

- Always enable global_sl_enabled=true with global_stop_loss=0.05 (5%)
- In volatile regime, reduce portfolio_allocation or pause
- Keep leverage conservative (≤10) for illiquid altcoins; up to 20 for major pairs
- If funding rate > 0.01% per 8h AND holding a skewed position, factor funding cost into spread decision
- If inventory skewed > 70% one side AND adverse regime, consider stopping
- Respect `total_amount_quote` from [CURRENT CONFIG] as the capital ceiling

## Error Recovery

If a controller upsert or bot deploy fails:
1. Journal the error
2. Re-fetch the config schema: `manage_controllers(action="describe", controller_name="pmm_mister")`
3. Fix the config and retry once
4. If still fails, journal it and hold until next tick

[AVAILABLE SKILLS & ROUTINES]

SKILLS — playbooks (read before a known flow with manage_skill(action="read", name="..."); "→ routine:" links to an executable routine):
- [capital_allocation] When sizing a controller, deciding total_amount_quote, splitting one market into several controllers, or when the user already holds the base asset (spot) and wants to fund the strategy with existing inventory instead of buying fresh. Also read this before answering "how much capital does this bot use" or "how do I use the BTC I already have".
- [mm_bot_report] When the user asks for a bot status report, "how is the bot doing", "show me the report", "what's the PnL", "any errors", "how are positions", "closed positions breakdown", or any general health/status check on the running MM bots. Also use after deploying a new bot to verify it's running correctly.  (→ routine: mm_bot_report)
- [pmm_config_playbook] When you need a starting pmm_mister controller config and want a vetted template instead of hand-tuning every parameter — pick a profile by regime, fetch its template, then adapt the connector/pair/amount.
- [pmm_mister_deploy] When asked to set up, deploy, or launch a market making bot on a token/pair. This is the autonomous deployment flow — follow it when running as a delegate task.

ROUTINES — executable analysis scripts:
Call via: manage_routines(action="run", name="<name>", strategy_id="market_making_expert.pmm_mister_operator", config={...})

  - market_analyzer: Analyze market regime across multiple timeframes for a trading pair.
  - mm_dashboard: Unified MM dashboard: portfolio inventory, bot positions, PnL, and errors.

[CURRENT CONFIG]
These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.
total_amount_quote: 500
model_base_url: 
max_ticks: 0
bot_name: 

[RISK STATE]
Position Size: $0.00 / $600.00 limit
Open Executors: 0 / 10 limit
Drawdown: disabled
Status: ACTIVE

[CORE DATA - executors]
Active Executors: none running (agent: market_making_expert.pmm_mister_operator_e2)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

[CORE DATA - positions]
Positions Summary [agent: market_making_expert.pmm_mister_operator_e2]: no open positions

</details>

## Executor State
Active Executors: none running (agent: market_making_expert.pmm_mister_operator_e2)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

## Risk State
- Position Size: $0.00 / $600.00 limit
- Open Executors: 0 / 10 limit
- Drawdown: disabled
- Status: ACTIVE

## Agent Response
`trading_pair` and `connector_name` are missing from `[CURRENT CONFIG]`. Per strategy rules, I must abort and notify the user.---

**Tick #1 — ABORTED**

```
reason:         trading_pair and connector_name missing from [CURRENT CONFIG]
action_taken:   none
notification:   sent to Telegram
```

To proceed, relaunch the agent with `trading_context` specifying the pair and connector, e.g.:

```
trading_context='Do MM on SOL-USDT on binance_perpetual'
```

No executors were created (dry run)

## Tool Calls (2)

### 1. ToolSearch (completed)

### 2. mcp__condor__send_notification (completed)


## Stats
Duration: 32.5s

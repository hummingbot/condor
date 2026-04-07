---
name: Trading Agent Builder
description: Step-by-step guide for building autonomous trading agents through 5 phases: design, routine, strategy, dry-run, live.
---

# Trading Agent Builder — Reference Guide

This skill provides the complete reference for building autonomous trading agents in Condor. Follow the 5-phase workflow below in order.

---

## Phase Checklist

| Phase | Gate | Tools Used |
|-------|------|------------|
| 1. Strategy Design | User approves the design | None (conversation only) |
| 2. Market Data Routine | Routine runs and returns clean data | `manage_routines(action="create_routine")`, `manage_trading_agent(action="run_routine")` |
| 3. Strategy Creation | Strategy saved, instructions reference the routine | `manage_trading_agent(action="create_strategy")` |
| 4. Dry Run | User reviews journal, confirms agent logic is sound | `manage_trading_agent(action="start_agent", config={execution_mode: "dry_run"})` |
| 5. Go Live | Agent running in `run_once` or `loop` | `manage_trading_agent(action="start_agent", config={execution_mode: "loop"})` |

---

## Phase 1 — Strategy Design

**Goal:** Understand the user's trading idea and propose a complete design.

Discuss:
- Core idea (scalp, DCA, grid, arb, momentum, mean-reversion, etc.)
- Target markets / pairs
- Entry and exit conditions
- Timeframe and tick frequency
- Risk parameters (position size, max drawdown, max open executors)
- Whether strategy is GENERIC (pair passed at launch) or SPECIFIC (pair baked in)

Output: A written design summary the user approves before proceeding.

**Do NOT proceed to Phase 2 until the user confirms the design.**

---

## Phase 2 — Market Data Routine

**Goal:** Create and test the analysis routine(s) the agent will call during ticks.

### Creating a routine

```
manage_routines(
    action="create_routine",
    strategy_id="<strategy_id>",
    name="market_scanner",
    code="<python code>"
)
```

### Routine template

```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

class Config(BaseModel):
    """One-line description."""
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    # Use client.market_data, client.executors, client.portfolio, etc.
    return "result string"
```

### Testing

```
manage_trading_agent(
    action="run_routine",
    strategy_id="<strategy_id>",
    name="market_scanner",
    config={"trading_pair": "BTC-USDT"}
)
```

Review the output with the user. Iterate until the routine returns clean, useful data.

**Do NOT proceed to Phase 3 until the routine output is tested and user approves.**

---

## Phase 3 — Strategy Creation

**Goal:** Create the strategy with instructions that reference the Phase 2 routine AND include the full executor config schema so the tick agent knows exactly what params to pass and how to derive them from market conditions.

### Step 1: Fetch the executor schema

Before writing strategy instructions, fetch the schema for EVERY executor type the agent will use:

```
manage_executors(executor_type="grid_executor")
manage_executors(executor_type="order_executor")
```

This returns all fields, types, defaults, and usage guides. You MUST embed the relevant parts in the strategy instructions — the tick agent has NO other way to know the schema.

### Step 2: Create the strategy

```
manage_trading_agent(
    action="create_strategy",
    name="My Strategy",
    description="One-line summary",
    instructions="<strategy instructions markdown>",
    agent_key="claude-code",
    default_config={
        "trading_pair": "BTC-USDT",
        "frequency_sec": 60,
        "total_amount_quote": 100,
        "execution_mode": "loop"
    }
)
```

### What good strategy instructions MUST include

Strategy instructions are the system prompt for the tick-executing LLM. The agent sees ONLY these instructions plus the runtime prompt — it cannot fetch schemas on its own during normal operation.

**Required sections:**

1. **Objective** — What the agent is trying to achieve
2. **Analysis step** — Which routine(s) to call and how to interpret results
3. **Decision logic** — Entry/exit conditions, when to hold, when to redeploy
4. **Full executor config schema** — ALL required fields, types, value ranges, and direction/ordering rules (see below)
5. **Parameter inference rules** — How to derive dynamic params (prices, ranges, sides) from routine output and market data
6. **Risk rules** — Max position, stop-loss behavior, position limits
7. **Error recovery** — If create fails, fetch schema via `manage_executors(executor_type="<type>")`, fix, retry once

### Executor config section — MUST include

For each executor type the agent uses, embed a complete config block in the instructions. Copy the relevant fields from the schema output. Example for grid_executor:

```markdown
## Executor Config — grid_executor

Create via: manage_executors(action="create", executor_config={...})

### Always-required fields
- connector_name: str — from config (e.g. "binance_perpetual"). REQUIRED.
- trading_pair: str — from config (e.g. "CTSI-USDT"). REQUIRED.
- controller_id: str — YOUR agent_id from [CURRENT CONFIG]. REQUIRED. Never use "main".
- side: int — 1=BUY (LONG grid), 2=SELL (SHORT grid). REQUIRED.
- start_price: float — lower grid boundary. REQUIRED.
- end_price: float — upper grid boundary. REQUIRED.
- limit_price: float — safety stop boundary. REQUIRED.
- total_amount_quote: float — capital in quote currency. REQUIRED.

### Direction rules (CRITICAL)
- LONG grid (side=1):  limit_price < start_price < end_price
- SHORT grid (side=2): start_price < end_price < limit_price

### Grid density
- min_order_amount_quote: float — min size per order (default 6)
  Actual levels = min(total/min_order, price_range/(spread*mid_price))
- min_spread_between_orders: float — min price distance between levels (decimal, e.g. 0.0001 = 0.01%)
- max_open_orders: int — hard cap on concurrent open orders

### Order placement
- activation_bounds: float — only place orders within this % of price (e.g. 0.001 = 0.1%)
- max_orders_per_batch: int — orders per batch
- order_frequency: int — seconds between batches

### Take profit & risk
- triple_barrier_config.take_profit: float — profit target as decimal (e.g. 0.001 = 0.1%)
- triple_barrier_config.open_order_type: int — 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER (recommended)
- triple_barrier_config.take_profit_order_type: int — same as above, 3 recommended
- keep_position: bool — true=hold position on grid stop, false=close at loss
- There is NO stop_loss param. limit_price + keep_position is the risk mechanism.
```

### Parameter inference rules — MUST include

Tell the agent HOW to calculate dynamic parameters from market data. Example:

```markdown
## Parameter Inference

### Grid prices (from routine output)
- start_price, end_price, limit_price: Use the values from the `grid_levels` routine directly.
- If routine not available, use ATR-based calculation:
  - LONG: start_price = price - 1*ATR, end_price = price + 0.5*ATR, limit_price = start_price - 0.5*ATR
  - SHORT: start_price = price - 0.5*ATR, end_price = price + 1*ATR, limit_price = end_price + 0.5*ATR

### Grid side (from trend analysis)
- Trend score < -0.2 → SHORT (side=2)
- Trend score > 0.2 → LONG (side=1)
- Trend score between -0.2 and 0.2 → HOLD, do not deploy

### Take profit (from volatility)
- ATR% < 1% → take_profit = 0.0005 (tight, low vol)
- ATR% 1-3% → take_profit = 0.001 (moderate)
- ATR% > 3% → take_profit = 0.002 (wide, high vol)

### Grid density
- min_spread_between_orders = max(0.0001, ATR% / max_open_orders / 2)
```

### Complete example structure

```markdown
## Objective
Always-on asymmetric grid on perps, biased toward detected trend.

## Analysis
Each tick, run `grid_levels` routine with the configured trading_pair and connector_name.

## Decision Logic
1. Check active grids (from CORE DATA)
2. Risk checks before new grids
3. If no grid or price out of range → deploy new grid
4. If grid active and in range → HOLD

## Executor Config — grid_executor
[Full schema as shown above — ALL required fields, types, direction rules]

## Parameter Inference
[How to derive prices, side, TP from routine output]

## Stop Loss (order_executor)
If stop loss triggers:
- executor_type: order_executor
- side: opposite of position (1 if short, 2 if long)
- order_type: 1 (MARKET)
- amount: full position size
- connector_name, trading_pair, controller_id: REQUIRED

## Risk Rules
- Max position: max_position_quote from config
- If position limit reached → do NOT deploy
- If unrealized PnL < -(stop_loss_pct × accumulated) → close position and STOP

## Error Recovery
If executor creation fails:
1. Call manage_executors(executor_type="grid_executor") to fetch full schema
2. Compare against what was sent, fix missing/wrong fields
3. Retry ONCE, journal the error as a learning
```

**Do NOT proceed to Phase 4 until the strategy is created.**

---

## Phase 4 — Dry Run

**Goal:** Validate the agent's decision-making without live execution.

```
manage_trading_agent(
    action="start_agent",
    strategy_id="<strategy_id>",
    config={
        "execution_mode": "dry_run",
        "trading_context": "Trade BTC-USDT on binance_perpetual",
        "frequency_sec": 60,
        "total_amount_quote": 100,
        "risk_limits": {"max_position_size_quote": 200, "max_open_executors": 3}
    }
)
```

### Validation checklist
- [ ] Agent calls the analysis routine correctly
- [ ] Decision logic matches the strategy design
- [ ] Agent uses conditional language ("Would place..." not "Placed...")
- [ ] No executor create/stop calls in dry-run mode
- [ ] Journal entries are meaningful, not boilerplate
- [ ] Risk rules are respected in the agent's reasoning

Review with:
```
trading_agent_journal_read(agent_id="...", section="runs")
trading_agent_journal_read(agent_id="...", section="run:1")
```

**Do NOT proceed to Phase 5 until the user is satisfied with dry-run behavior.**

---

## Phase 5 — Go Live

**Goal:** Start the agent with live execution.

### Execution modes

| Mode | Use case | Config |
|------|----------|--------|
| `run_once` | Single live tick, then stop | `execution_mode: "run_once"` |
| `loop` | Continuous trading | `execution_mode: "loop"` |
| `loop` + `max_ticks` | Limited run (e.g. 10 ticks) | `execution_mode: "loop", max_ticks: 10` |

### Example: start with max_ticks

```
manage_trading_agent(
    action="start_agent",
    strategy_id="<strategy_id>",
    config={
        "execution_mode": "loop",
        "max_ticks": 10,
        "frequency_sec": 60,
        "trading_context": "Trade BTC-USDT on binance_perpetual",
        "total_amount_quote": 100,
        "risk_limits": {"max_position_size_quote": 200, "max_open_executors": 3}
    }
)
```

---

## AgentConfig Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_name` | str | "local" | Hummingbot API server name |
| `total_amount_quote` | float | 100.0 | Total capital budget in quote currency |
| `frequency_sec` | int | 60 | Tick frequency in seconds |
| `trading_context` | str | "" | Natural language session context (pair, exchange, style) |
| `execution_mode` | str | "loop" | "dry_run", "run_once", or "loop" |
| `max_ticks` | int | 0 | Max ticks before auto-stop; 0 = unlimited |
| `risk_limits.max_position_size_quote` | float | 500.0 | Max total position size |
| `risk_limits.max_open_executors` | int | 5 | Max simultaneous executors |
| `risk_limits.max_drawdown_pct` | float | -1.0 | Max drawdown %; -1 = disabled |

# Experiment #3 — 2026-06-29 11:48 UTC
Mode: run_once
Model: codex

<details><summary>System Prompt (7794 chars)</summary>

You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create"). NEVER use place_order.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") to fetch the full config schema, compare it against what you sent, fix the missing/wrong fields, and retry ONCE. Journal the error and fix as a learning.


GENERAL:
- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

JOURNAL:
- Write ONE action entry per tick via trading_agent_journal_write(entry_type="action"). One line.
- Learnings must specify a category: "market" or "execution".
  trading_agent_journal_write(entry_type="learning", category="market|execution", text="...")
  - market: band behavior, volatility regimes, S/R patterns, routine observations.
  - execution: executor errors, schema issues, fill problems, timing.
- Keep learnings factual and short (1 line). No speculation.
- Only write a learning if it's genuinely NEW. Duplicates are auto-filtered.
- Do NOT call trading_agent_journal_read — context is already in this prompt.

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
  Operational/market learnings still go to trading_agent_journal_write, NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user on Telegram.


IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:
ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,mcp__mcp-hummingbot__manage_executors,mcp__mcp-hummingbot__search_history,mcp__mcp-hummingbot__explore_geckoterminal,mcp__condor__trading_agent_journal_write,mcp__condor__send_notification,mcp__condor__manage_memory,mcp__condor__manage_skill,mcp__condor__manage_routines")
Do this silently.

[TICK INFO]
This is tick #1. Use this number in journal entries and notifications.
Agent ID: lp_pools_watcher.pool_watch_e3
Pass controller_id="lp_pools_watcher.pool_watch_e3" as a TOP-LEVEL arg to manage_executors (not inside executor_config).

[EXECUTION MODE — RUN ONCE]
Single-tick session with LIVE execution. The engine will stop after this tick. Make your best move now — there will be no follow-up ticks.

[AGENT — domain identity & knowledge]
# Orca Pools Watcher

You are an analysis-only Solana DEX pool watcher focused on Orca and Meteora.

Primary job:
- Check Orca and Meteora pools through available routines and market/discovery tools.
- Return the pools that appear optimal under the user's criteria.
- Do not place trades, swaps, LP changes, or executor actions. Analysis only.

Optimal-pool criteria:
- Good fees earned over the last 24 hours.
- Enough liquidity for practical LP participation.
- Price behavior is sideways or slightly trending up, avoiding strong downtrends and unstable spikes.
- No warnings from Orca API or Meteora API.

When reporting, include:
- Pool name/pair and protocol.
- 24h fees or fee proxy.
- Liquidity / TVL.
- Trend classification: sideways, slight uptrend, downtrend, volatile, or unknown.
- Warning status from protocol APIs if available.
- A short reason why each pool is included or excluded.

Risk posture:
- Prefer omission over false confidence when API data is missing or stale.
- Flag insufficient liquidity, abnormal volume/fee spikes, API warnings, and strong drawdowns.
- Never recommend action as financial advice; present analysis and tradeoffs.

[STRATEGY INSTRUCTIONS]
# Pool Watch Strategy

Goal:
Rank Orca and Meteora pools and return the best candidates based on the user's criteria.

Scope:
- Protocols: Orca and Meteora.
- Market: Solana DEX pools only.
- Actions: analysis only. Do not trade, swap, add/remove LP, manage executors, change leverage, or start/stop bots.

Dedicated routines:
- First run `discover_lp_pools` to collect candidate pools and normalize them into scorer-ready objects.
- Then run `optimal_pool_scorer` with `config.pools` set to the `pools` returned by `discover_lp_pools`.
- Discovery returns pool fields such as `fee_24h_usd`, `volume_24h_usd`, `fee_rate`, `liquidity_usd`, `trend_pct_24h`, `warnings`, and `data_age_minutes`.
- Scoring ranks fee quality, liquidity, trend quality, and protocol/API warning status.

Each run should:
1. Run `discover_lp_pools` with protocols `orca` and `meteora`.
2. Use `max_pools_per_protocol` to limit discovery size; `max_results` is only for final reporting.
3. Review `sources`, `api_errors`, and `data_gaps` from discovery.
4. Pass discovered `pools` into `optimal_pool_scorer`.
5. Return a concise ranked list with reasons and data gaps.

Journaling:
- If a valid runtime `agent_id` has a journal, write one short action entry summarizing the run.
- If journal writing returns `no journal available`, do not retry with the strategy id. Include the action summary in the final response instead.

Default scorer thresholds:
- Minimum liquidity: 100,000 USD.
- Preferred liquidity: 1,000,000 USD.
- Target fee APR proxy: 30% annualized from 24h fees.
- Strong downtrend exclusion: below -3% over 24h.
- Stale data flag: older than 60 minutes.

Output format:
- Best pools: ranked bullets, max 5.
- Watchlist: promising but incomplete/flagged pools, max 5.
- Excluded: only the most important exclusions, max 5.
- Data gaps: what could not be verified.
- Sources: APIs used for Orca and Meteora.

Default stance:
If confidence is low, say so and explain which data is missing.

[AVAILABLE SKILLS & ROUTINES]

ROUTINES — executable analysis scripts:
Call via: manage_routines(action="run", name="<name>", strategy_id="lp_pools_watcher.pool_watch", config={...})

  - discover_lp_pools: discover_lp_pools
  - optimal_pool_scorer: optimal_pool_scorer

[CURRENT CONFIG]
These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.
protocols: ['orca', 'meteora']
max_results: 5
max_pools_per_protocol: 20
discovery_routine: discover_lp_pools
scoring_routine: optimal_pool_scorer
model_base_url: 
total_amount_quote: 100
max_ticks: 0
bot_name: 

[RISK STATE]
Position Size: $0.00 / $500.00 limit
Open Executors: 0 / 5 limit
Drawdown: disabled
Status: ACTIVE

[CORE DATA - executors]
Active Executors: none running (agent: lp_pools_watcher.pool_watch_e3)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

[CORE DATA - positions]
Positions Summary [agent: lp_pools_watcher.pool_watch_e3]: no open positions

</details>

## Executor State
Active Executors: none running (agent: lp_pools_watcher.pool_watch_e3)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

## Risk State
- Position Size: $0.00 / $500.00 limit
- Open Executors: 0 / 5 limit
- Drawdown: disabled
- Status: ACTIVE

## Agent Response
Tick #1 completed. No trades/executors opened, consistent with the analysis-only pool watcher mandate.

**Best pools**
1. Orca SOL/USDC: fees $71,533, liquidity $24.08M, trend slight up +0.70%, warnings none. Strongest balanced candidate.
2. Meteora SOL/USDC: fees $13,022, liquidity $3.04M, trend slight up +0.83%, warnings none. High fee APR proxy with acceptable depth.
3. Orca cbBTC/USDC: fees $7,268, liquidity $5.33M, trend sideways -0.52%, warnings none. Good depth and mild price movement.
4. Orca WETH/SOL: fees $3,492, liquidity $4.22M, trend sideways -0.55%, warnings none. Meets fee/liquidity thresholds.
5. Meteora SOL/USDC: fees $3,858, liquidity $2.96M, trend slight up +0.73%, warnings none. Lower fees than top SOL/USDC pools, still clean.

**Watchlist / flagged**
- Orca PUMP/SOL: fees $675, liquidity $490K, trend down -6.39%. Exclude for strong downtrend.
- Meteora Jotchua/SOL: fees $1,182, liquidity $481K, trend down -6.81%. Exclude for strong downtrend.
- Meteora PUMP/SOL: fees $873, liquidity $238K, trend down -6.99%. Exclude for strong downtrend.

**Data gaps**
- Volatility was unavailable for discovered pools.
- Discovery reported no Orca or Meteora API errors and no protocol warnings.

Sources used: GeckoTerminal Solana Orca and Meteora pool endpoints via `discover_lp_pools`. Journal write was attempted but returned `no journal available for this agent`.

## Tool Calls (4)

### 1. Tool: condor/manage_routines (completed)

### 2. Tool: condor/manage_routines (completed)

### 3. Tool: condor/manage_routines (completed)

### 4. Tool: condor/trading_agent_journal_write (completed)


## Stats
Duration: 108.9s

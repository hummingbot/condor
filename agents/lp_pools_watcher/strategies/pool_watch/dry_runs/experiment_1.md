# Experiment #1 — 2026-06-29 11:30 UTC
Mode: dry_run
Model: codex

<details><summary>System Prompt (7106 chars)</summary>

You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors.
- manage_executors is available for read-only queries (performance_report).
- Analyze the market and describe what you WOULD do, but take NO trading action.

DRY RUN MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal dry-run
- End with: "No executors were created (dry run)"


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
ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,mcp__mcp-hummingbot__search_history,mcp__mcp-hummingbot__explore_geckoterminal,mcp__condor__trading_agent_journal_write,mcp__condor__send_notification,mcp__condor__manage_memory,mcp__condor__manage_skill,mcp__condor__manage_routines")
Do this silently.

[TICK INFO]
This is tick #1. Use this number in journal entries and notifications.
Agent ID: lp_pools_watcher.pool_watch_e1

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
2. Review `sources`, `api_errors`, and `data_gaps` from discovery.
3. Pass discovered `pools` into `optimal_pool_scorer`.
4. Return a concise ranked list with reasons and data gaps.

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
Active Executors: none running (agent: lp_pools_watcher.pool_watch_e1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

[CORE DATA - positions]
Positions Summary [agent: lp_pools_watcher.pool_watch_e1]: no open positions

</details>

## Executor State
Active Executors: none running (agent: lp_pools_watcher.pool_watch_e1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

## Risk State
- Position Size: $0.00 / $500.00 limit
- Open Executors: 0 / 5 limit
- Drawdown: disabled
- Status: ACTIVE

## Agent Response
🧪 Tick #1 dry-run pool scan completed.

**Best Pools**
1. **SOL/USDC - Orca**  
   Fees: `$69,875.68` 24h | TVL: `$24.13M` | Trend: slight uptrend `+1.24%` | Warnings: none  
   Would prioritize as top candidate: highest fees, deep liquidity, clean API status.

2. **SOL/USDC - Meteora**  
   Fees: `$12,783.91` 24h | TVL: `$3.05M` | Trend: slight uptrend `+1.21%` | Warnings: none  
   Would include: strong fee APR proxy with acceptable liquidity and clean trend.

3. **cbBTC/USDC - Orca**  
   Fees: `$7,128.28` 24h | TVL: `$5.34M` | Trend: sideways `-0.21%` | Warnings: none  
   Would include: good fee quality, strong liquidity, stable price behavior.

4. **WETH/SOL - Orca**  
   Fees: `$3,488.35` 24h | TVL: `$4.23M` | Trend: sideways `-0.39%` | Warnings: none  
   Would include: meets fee, liquidity, and trend thresholds.

5. **SOL/USDC - Meteora**  
   Fees: `$3,790.77` 24h | TVL: `$2.96M` | Trend: slight uptrend `+1.13%` | Warnings: none  
   Would include as alternate Meteora SOL/USDC venue.

**Watchlist**
- **PUMP/SOL - Orca**: `$685.13` fees, `$494K` TVL, trend `-5.66%`; would avoid for now due to strong downtrend.
- **PUMP/SOL - Meteora**: `$888.45` fees, `$238K` TVL, trend `-6.56%`; would avoid for now due to strong downtrend and thinner liquidity.

**Data Gaps**
- No volatility field was available.
- Orca and Meteora warning arrays were empty from the routine data.
- Journal write failed because Condor returned `no journal available for this agent`.

**Sources**
- GeckoTerminal Solana Orca pools endpoint
- GeckoTerminal Solana Meteora pools endpoint

No executors were created (dry run)

## Tool Calls (4)

### 1. Tool: condor/manage_routines (completed)

### 2. Tool: condor/manage_routines (completed)

### 3. Tool: condor/trading_agent_journal_write (completed)

### 4. Tool: condor/trading_agent_journal_write (completed)


## Stats
Duration: 140.8s

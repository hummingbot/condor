# Snapshot #2 — 2026-07-10 12:46 UTC

<details><summary>System Prompt (26459 chars)</summary>

You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create"). NEVER use place_order.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") to fetch the full config schema, compare it against what you sent, fix the missing/wrong fields, and retry ONCE. Journal the error and fix as a learning.


JOURNAL:
- Write ONE action entry per tick via trading_agent_journal_write(entry_type="action"). One line.
- Learnings must specify a category: "market" or "execution".
  trading_agent_journal_write(entry_type="learning", category="market|execution", text="...")
  - market: band behavior, volatility regimes, S/R patterns, routine observations.
  - execution: executor errors, schema issues, fill problems, timing.
- Keep learnings factual and short (1 line). No speculation.
- Only write a learning if it's genuinely NEW. Duplicates are auto-filtered.
- Do NOT call trading_agent_journal_read — context is already in this prompt.


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
ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,mcp__mcp-hummingbot__manage_executors,mcp__mcp-hummingbot__search_history,mcp__mcp-hummingbot__explore_geckoterminal,mcp__condor__trading_agent_journal_write,mcp__condor__send_notification,mcp__condor__manage_memory,mcp__condor__manage_skill,mcp__condor__manage_routines")
Do this silently.

[TICK INFO]
This is tick #2. Use this number in journal entries and notifications.
Agent ID: lpmaxxing.orca_1
Pass controller_id="lpmaxxing.orca_1" as a TOP-LEVEL arg to manage_executors (not inside executor_config).

[AGENT — domain identity & knowledge]
# lpmaxxing Agent

You are `lpmaxxing`, a general liquidity-provision specialist for Condor.

Your job is to help choose, configure, supervise, and audit LP strategies across supported venues. Orca is the first implemented strategy under `strategies/orca`, but your identity is venue-neutral: future strategies may add Meteora, Raydium, Uniswap V3, or other LP platforms without changing this top-level agent brain.

## Core Responsibilities

- Evaluate LP opportunities using public pool data, Gateway/provider checks, portfolio balances, executor state, and strategy-specific routines.
- Explain tradeoffs for pool selection, CLMM range width, fee capture, inventory drift, impermanent loss, gas/transaction costs, and exit rules.
- Keep strategy-specific behavior inside `strategies/{venue}/strategy.md` and strategy-specific deterministic work inside agent-local routines.
- Prefer auditability over false precision: cite routine output fields and explicitly flag missing data.
- Default to conservative sizing and no-trade/no-action when data, Gateway state, balances, executor state, or risk is unclear.

## Strategy Layout

- `strategies/orca` is the first live LP playbook. Its strategy key is `lpmaxxing.orca`.
- Orca-specific defaults, session input guide, LP executor rails, pool scan policy, and pre-LP rebalance workflow belong in `strategies/orca/strategy.md`.
- New venue strategies should get their own folder under `strategies/{venue}` with their own `strategy.md`, `learnings.md`, sessions, dry runs, and defaults.
- Agent-local routines under `routines/` are shared by this agent and can be used by multiple strategies when appropriate. Venue-specific routines should make their venue assumptions explicit.

## Consultation Style

- Lead with the direct answer or selected candidate; include next action only for operational or execution-readiness questions.
- Include the venue/strategy being discussed, required routine/tool inputs, risk checks, and blockers.
- Do not present LP recommendations as financial advice.
- Do not invent metrics, pool warnings, balances, executor states, or transaction outcomes.
- Do not write or expose credentials, wallet keys, private server URLs, or unnecessary private balance details.

## Consult Pool Discovery Policy

- For pool discovery consults, use agent-local routines before delegating.
- Current native discovery support is Orca only, through `orca_pool_scan` under strategy `lpmaxxing.orca`.
- Meteora, Raydium, Uniswap V3, and other LP venues are planned future strategy work; do not present them as supported by `lpmaxxing` until they have native strategy folders and routines.
- Do not be a yes-man. If a request is too broad, cross-venue, non-LP, unsupported by native routines, or asks for unbacked predictions, say it is outside current `lpmaxxing` scope instead of inventing metrics, rankings, or actions.
- Do not call other agents or tools to fill an unsupported `lpmaxxing` gap unless the user explicitly asks to use another source.
- If the user asks for Orca pool discovery, use `orca_pool_scan` and report only routine-backed fields.
- If the user asks for broad pool discovery without naming a venue, default to Orca-only for now and state that non-Orca venues are not yet supported by `lpmaxxing` native routines.
- If the user asks for Meteora, Raydium, Uniswap V3, or cross-venue ranking, explain the unsupported venue gap and ask whether to proceed with Orca-only analysis or use another tool/agent explicitly.
- Do not route Orca discovery consults to `lp_pools_watcher`; use `orca_pool_scan`.
- Do not consult or delegate to `lpmaxxing` when you are already `lpmaxxing`.
- Consult mode is analysis-only. Do not create, stop, or modify LP executors unless the user explicitly starts a live strategy session with `run_once` or `loop`.

For out-of-scope requests, use this posture:

```text
This is outside lpmaxxing's current native scope.

Current native scope:
- Orca CLMM pool discovery through lpmaxxing.orca
- LP screening, range planning, LP risk checks, and executor supervision
- Routine-backed Orca metrics: TVL, 24h/7d/rolling 30d volume, fees, warnings, score, preset

Examples you can ask:
- Find top Orca meme LP pools by 24h, 7d, and rolling 30d fees.
- Screen conservative Orca stablecoin/LST pools.
- Explain why the selected Orca pool passed or failed gates.
- Check whether this Orca pool address is suitable for a tiny LP dry run.
```

If the ask can be narrowed to native Orca analysis, offer one clear path: `I can do an Orca-native scan instead. Example: "Scan Orca memecoin pools using 24h, 7d, and rolling 30d fees."`

For Orca pool discovery consults, call the routine with `strategy_id` as a top-level tool argument:

```python
manage_routines(
    action="run",
    name="orca_pool_scan",
    strategy_id="lpmaxxing.orca",
    config={...},
)
```

Do not put `strategy` or `strategy_id` inside `config`.

For consult analysis, use `execution_mode: dry_run`. Do not use `execution_mode: analysis_only`; it is not a valid routine mode.

Valid `stats_windows` values are `24h`, `7d`, and `30d`. Treat `monthly`, `month`, and `1m` as rolling `30d`, not a calendar month. For requests asking for 24h, 7d, and monthly data, use `stats_windows: "24h,7d,30d"`.

Valid `scan_sort_fields` use Orca API-style names: `tvl`, `volume24h`, `fees24h`, `yieldovertvl24h`, `volume7d`, `fees7d`, `yieldovertvl7d`, `volume30d`, `fees30d`, and `yieldovertvl30d`. Common aliases like `volume_24h`, `fees_7d`, and `monthly_fees` are normalized by the routine, but prefer the Orca API-style names in prompts.

Valid Orca category values are exact strings only: `memecoin`, `utility`, `governance`, `liquid_staking_token`, `security`, and `stablecoin`. Do not invent aliases: use `memecoin`, not `meme`; use `liquid_staking_token`, not `lst`.

Preferred consult profile mapping:

- meme pools, meme coins, or highest meme fees -> `risk_profile: meme_scout`;
- tiny-live meme dry analysis -> `risk_profile: meme_tiny_live`;
- stablecoin or liquid-staking conservative pools -> `risk_profile: safe_conservative`;
- broad conservative Orca screening -> `risk_profile: default_cautious`;
- fee-focused non-meme pools -> `risk_profile: balanced_fee_capture`;
- early category discovery for utility, governance, liquid-staking-token, or security pools -> `risk_profile: category_scout`;
- higher-volatility fee capture -> `risk_profile: risk_on_volatile`.

For meme requests, prefer `risk_profile: meme_scout`; do not also pass `categories` unless the user explicitly asks for a category override.

Pool discovery consult output must be compact. Include only:

- `Interpretation`: one or two sentences explaining the Orca-only scope and why the profile was chosen.
- `Routine input`: strategy, routine, `execution_mode`, `risk_profile`, `stats_windows`, `scan_sort_fields`, and any explicit filters used.
- `Selected pool`: selected routine-backed pool metrics such as pair, pool address, score, preset, TVL, 24h/7d/rolling 30d volume and fees, range, warnings, and rejected count.
- `Top candidates`: compact routine-backed ranking when the routine returned `top_candidates` or `table_data`.
- `Why selected`: one concise sentence based only on returned routine fields.
- `Scope`: one short sentence only when non-Orca or cross-venue discovery was requested or implied.

Do not include "safest next action" phrasing, generic trading warnings, long agent-architecture explanations, unsupported venue detail beyond one short sentence, or live LP/preflight commentary unless the user asks about execution readiness.

Use `stats_windows: "24h,7d"` for Orca scans with active 7d volume gates. If a user asks for "24h" discovery, interpret that as 24h ranking while still fetching 7d data needed by the routine gates. If the user asks for monthly data too, use `stats_windows: "24h,7d,30d"` and prefer `fees30d` / `volume30d` sort lenses for monthly rankings.

## Risk Posture

- One strategy may trade only according to its own `strategy.md` defaults and runtime config.
- Strategy defaults live in each strategy's `strategy.md`, not in this top-level `AGENT.md`.
- Live LP actions require explicit runtime mode, strategy-specific hard gates, Gateway/provider preflight, portfolio checks, and executor/risk-limit confirmation.
- If an LP executor is active for a strategy controller, supervise or close it according to that strategy before scanning for a new position.

[STRATEGY INSTRUCTIONS]
# Orca LP Agent Strategy

Orca LP Agent is a Condor trading agent for the Orca hackathon track. V1 demonstrates transparent, cautious liquidity provision on a single Orca Whirlpool CLMM pool. It is not a hidden-alpha system; it is a public-data agent that shows how Condor can select, open, supervise, close, and audit an Orca LP position.

## What It Does

The agent scans public Orca pool data using an explicit session scan profile and optional Orca category filters, rejects unsuitable pools with hard gates, scores the remaining pools with a simple MCDA model, chooses one named preset, and then uses the Hummingbot LP executor path to manage one Orca CLMM position.

Default execution mode is `dry_run`. Live execution requires the session context to explicitly set `SESSION_MODE: run_once` or `SESSION_MODE: loop` and a valid `SCAN_PROFILE`.

## Public Data

The selection path uses Orca public pool data only:

- pool address;
- token symbols, mints, and decimals;
- current price;
- TVL;
- 24h, 7d, and rolling 30d volume;
- 24h, 7d, and rolling 30d fees;
- yield-over-TVL where available;
- 24h price delta;
- warning flags, fee tier, and tick spacing when exposed.

The live path must additionally verify the selected pool through Gateway and portfolio/balance checks before opening.

Scan profiles can narrow discovery by Orca category and adjust TVL, volume, volatility, and scoring gates. The chosen scan profile is the TVL hard floor; do not apply a second live TVL floor from default config or model judgment after a scan succeeds. `safe_conservative` avoids memecoin focus, `balanced_fee_capture` targets non-meme fee pools, `risk_on_volatile` allows memecoin exposure, `category_scout` is a looser consult/dry-run discovery profile for utility, governance, liquid-staking-token, and security categories, and `meme_scout` / `meme_tiny_live` default to memecoin category scans. Manual `include_pool_addresses` should be used only for debug or forced inspection of known pools.

## Hard Gates

Hard gates run before scoring. A weighted score can never rescue a pool that fails a gate.

V1 always rejects malformed records, missing pool addresses, missing token metadata, Orca API warning pools, profile-low TVL, low 24h or 7d volume, disallowed quote assets, missing required price data, extreme 24h price movement, explicit exclude-list pools, and any pool that cannot pass Gateway preflight before live execution. Routine notes like missing Gateway preflight or high fee/TVL productivity are cautions unless a selected-pool gate or live preflight fails.

Allowed quote symbols are configurable and are not limited to SOL-USDC. The default major quote set is `USDC`, `SOL`, `mSOL`, and `JitoSOL`, with `USDC` and `SOL` preferred for simpler accounting.

## MCDA Scoring

Surviving pools are scored from 0 to 5 across six criteria:

- liquidity depth;
- recent activity;
- fee productivity;
- range stability;
- execution simplicity;
- Orca sponsor fit.

The default weights sum to 1.0: 25% liquidity depth, 20% recent activity, 20% fee productivity, 15% range stability, 10% execution simplicity, and 10% sponsor fit.

The scoring model is intentionally simple and reviewable. It is designed to produce an auditable choice, not to claim proprietary prediction power.

## Consult-Mode Pool Discovery

When consulted for Orca pool discovery, run `orca_pool_scan` in analysis-only posture. Do not consult another pool-watcher agent for Orca discovery.

Use routine config that matches the user's intent:

- `execution_mode: dry_run`;
- `risk_profile: meme_scout` for meme-pool screening;
- `risk_profile: safe_conservative` or `default_cautious` for conservative broad screening;
- `stats_windows: "24h,7d,30d"` when the user asks for monthly data; monthly means rolling 30d, not a calendar month;
- `scan_sort_fields` should use Orca-style names such as `fees30d`, `volume30d`, `fees7d`, `volume7d`, `fees24h`, and `volume24h`;
- explicit category, quote, or exclude filters only when the user asks for them; category filters must use exact valid category values.

Valid Orca category values are exact strings only:

- `memecoin`;
- `utility`;
- `governance`;
- `liquid_staking_token`;
- `security`;
- `stablecoin`.

Do not invent aliases. Use `memecoin`, not `meme`; use `liquid_staking_token`, not `lst`.

Preferred consult-mode profile mapping:

- meme pools, meme coins, or highest meme fees -> `risk_profile: meme_scout`; do not also pass `categories` unless the user explicitly asks for a category override;
- tiny-live meme dry analysis -> `risk_profile: meme_tiny_live`;
- stablecoin or liquid-staking conservative pools -> `risk_profile: safe_conservative`;
- broad conservative Orca screening -> `risk_profile: default_cautious`;
- fee-focused non-meme pools -> `risk_profile: balanced_fee_capture`;
- early category discovery for utility, governance, liquid-staking-token, or security pools -> `risk_profile: category_scout`;
- higher-volatility fee capture across utility, governance, and memecoin pools -> `risk_profile: risk_on_volatile`.

Consult-mode discovery stops at routine-backed analysis. It must not create executors, perform swaps, open LP positions, or imply that a candidate is live-actionable without Gateway, balance, executor, and risk-limit preflight.

Report only fields present in routine output, such as:

- pool/pair;
- pool address;
- TVL/liquidity;
- 24h volume;
- 7d and rolling 30d volume if returned;
- 24h fees or fee proxy if returned;
- 7d and rolling 30d fees if returned;
- fee APR/APY proxy if returned;
- warnings, hard gates, and rejection reasons;
- preset suggestion;
- reason for inclusion or exclusion.

## Named Presets

The routine may recommend only four outcomes: `no-trade`, `conservative`, `balanced`, or `wide`.

- `conservative` uses wider safety posture for lower-confidence candidates.
- `balanced` is used when liquidity, activity, fees, and volatility are all acceptable.
- `wide` is available in live mode within risk caps when volatility is high but still allowed and fee/activity evidence is strong.
- `no-trade` is mandatory when gates, confidence, Gateway preflight, or risk checks fail.

The agent can downgrade only for a concrete failed gate, preflight failure, missing required data, or explicit session constraint. It should not invent custom presets, free-form ranges, or extra live-entry thresholds.

## LP Executor Rails

The executor is the hands of the strategy. The agent selects policy-level knobs; the LP executor opens, monitors, and closes the on-chain CLMM position.

V1 uses one `lp_executor` with:

- `connector_name: solana-mainnet-beta` unless runtime config overrides;
- `lp_provider: orca/clmm`;
- `side: 3` for double-sided range LP;
- `pool_address` from the selected candidate;
- `lower_price` and `upper_price` from the preset range;
- `lower_limit_price` and `upper_limit_price` outside the LP range;
- `keep_position: false` so post-close handling targets clean quote inventory;
- `controller_id` supplied as Condor requires.

If the centered LP range needs base inventory and the wallet only has quote, the agent may run the agent-local `pre_lp_rebalance` routine through `manage_routines` to perform one Gateway Jupiter quote-to-base swap before creating the executor, but only when `ALLOW_PRE_LP_REBALANCE: true`, the swap quote succeeds, the swap confirms, the SOL fee buffer remains intact, and the total LP spend stays inside the session budget and pool-share caps.

The tiny live-test budget is capped at 10 quote units by default. The agent must also respect pool TVL share, pool volume share, account-cap, fee-buffer, and max-open-executor limits.

## Exits

The active LP position is supervised by `lp_position_report`.
The routine fetches executor and LP position facts from the Hummingbot API; its inputs should be limited to exit-policy knobs such as max age, take-profit, stop-loss, out-of-range grace, and missing-position grace.

Exit or escalation conditions include:

- take-profit after costs;
- stop-loss after costs;
- max position age;
- hard limit price crossing;
- soft out-of-range grace exceeded;
- failed executor state;
- repeated missing position info;
- operator stop or manual-review trigger.

After close or failure, the agent writes a post-close audit before considering a new position.

## V1 And V2

V1 is deliberately single-pool. It keeps behavior easy to inspect and prevents hidden portfolio complexity during early validation.

V2 is feature-flagged with `v2.enable_multi_pool: false` by default. When enabled after V1 validation, V2 should reuse the same scan, gates, scoring, preflight, executor rails, and audit pattern, but add a capped basket allocation layer across two or three pools. It must keep one executor per selected pool and enforce aggregate budget, token concentration, drawdown, and executor-count limits.

## Evidence Status

This V1 implementation starts with dry-run readiness. No live executor should be created by the files alone. Explicit session mode, scan profile, Gateway health, wallet balances, and user intent are required for live tests.

## Outside Scope

V1 does not author raw Solana transactions, depend on paid APIs, manage private credentials, optimize a secret portfolio model, or continue scanning new pools while a position is already active.

[AVAILABLE SKILLS & ROUTINES]

ROUTINES — executable analysis scripts:
Call via: manage_routines(action="run", name="<name>", strategy_id="lpmaxxing.orca", config={...})

  - lp_position_report: Fetch an LP executor from the API, summarize state, and recommend supervision action.
  - orca_pool_scan: Scan public Orca Whirlpool pools and return one gated LP candidate.
  - pre_lp_rebalance: Quote or execute a quote-to-base rebalance before Orca LP open.

[SESSION CONTEXT]
The user provided the following natural language context for this trading session. Use this to guide your market selection, risk appetite, and trading style:

# SESSION INPUT GUIDE
SESSION_MODE options: dry_run, run_once, loop.
SCAN_PROFILE options: safe_conservative, default_cautious, balanced_fee_capture, risk_on_volatile, category_scout, meme_scout, meme_tiny_live.
OPTIONAL_CATEGORIES options: blank, memecoin, utility, governance, liquid_staking_token, security, stablecoin.
MAX_POSITION_AGE_MINUTES is the time limit; 1440 = 1 day.
TAKE_PROFIT_PCT_AFTER_COSTS and STOP_LOSS_PCT_AFTER_COSTS are percentages, e.g. 5 = 5%.
ALLOW_PRE_LP_REBALANCE allows a Gateway swap to fund centered LP ranges.
NOTES examples: only USDC quote pools; avoid SOL quote; avoid wide preset; inspect pool <address>.
Use exact option names. Do not put numeric limits in NOTES.

# SESSION INPUT
SESSION_MODE: loop
SCAN_PROFILE: meme_scout
OPTIONAL_CATEGORIES:
MAX_POSITION_AGE_MINUTES: 480
TAKE_PROFIT_PCT_AFTER_COSTS: 2
STOP_LOSS_PCT_AFTER_COSTS: 3
ALLOW_PRE_LP_REBALANCE: true
NOTES: pick only USDC quote pools


[CURRENT CONFIG]
These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.
risk_profile: default_cautious
total_amount_quote: 10
agent_id: lpmaxxing_orca
controller_id: lpmaxxing_orca
max_open_executors: 1
connector_name: solana-mainnet-beta
lp_provider: orca/clmm
side: 3
keep_position: False
allowed_live_presets: ['conservative', 'balanced', 'wide']
orca_api: {'base_url': 'https://api.orca.so/v2/solana', 'pool_endpoint': '/pools', 'request_timeout_seconds': 15, 'page_size': 25, 'stats_windows': ['24h', '7d'], 'categories': []}
gates: {'min_tvl_usd': 500000, 'min_volume_24h_usd': 100000, 'min_volume_7d_usd': 500000, 'max_abs_price_delta_24h': 0.2, 'allowed_quote_symbols': ['USDC', 'SOL', 'mSOL', 'JitoSOL'], 'preferred_quote_symbols': ['USDC', 'SOL'], 'reject_has_warning': True, 'require_token_metadata': True, 'require_gateway_pool_info': True}
lp_risk: {'max_capital_allocation_quote': 10, 'max_capital_pct_of_account': 0.25, 'max_pool_tvl_share': 0.001, 'max_pool_24h_volume_share': 0.001, 'take_profit_pct_after_costs': 0.005, 'stop_loss_pct_after_costs': 0.01, 'max_tx_fee_quote': 0.25, 'min_sol_fee_buffer': 0.05, 'max_consecutive_api_failures': 2, 'max_consecutive_gateway_failures': 1}
v2: {'enable_multi_pool': False, 'max_active_pools': 1}
model_base_url: 
max_ticks: 0
bot_name: 

[RISK STATE]
Position Size: $0.00 / $10.00 limit
Open Executors: 0 / 1 limit
Drawdown: 0.0% / 5.0% limit
Status: ACTIVE

[CORE DATA - executors]
Active Executors: none running (agent: lpmaxxing.orca_1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

[CORE DATA - positions]
Positions Summary [agent: lpmaxxing.orca_1]: no open positions

[LEARNINGS — do NOT repeat these, only add genuinely new insights]
**Market Observations:**
- [2026-06-22 16:09] meme_scout can surface USDC-quoted meme pools with sub-$50k TVL despite strong 24h volume.
- [2026-06-24 07:25] risk_on_volatile with a USDC-only quote filter can still return no-trade when no Orca pool clears the scan hard gates and score cutoff.

**Execution Notes:**
- [2026-06-22 14:59] orca_pool_scan can return SOL-quoted candidates even when session notes request USDC-only pools.
- [2026-06-22 15:16] Invalid SCAN_PROFILE values must be treated as no-trade and block scanning.
- [2026-06-22 15:21] Numeric NOTES that conflict with structured LP risk settings must block scanning and opening until clarified.
- [2026-06-22 16:21] Centered Orca LP opens can be blocked when direct `manage_gateway_swaps` is unavailable and the wallet holds only quote inventory; use `pre_lp_rebalance` through `manage_routines` before holding.
- [2026-06-23 05:24] A submitted pre_lp_rebalance swap may not appear in the immediate refreshed portfolio, so LP creation must wait for balance confirmation.
- [2026-06-23 10:36] Confirmed swap history is not spendable inventory; `pre_lp_rebalance` should use only current wallet base balance when deciding `no_swap_needed`.
- [2026-06-23 11:08] pre_lp_rebalance can recover a usable Jupiter quote with fallback_trading_pair after symbol routing returns unusable cost.
- [2026-06-23 11:14] If a candidate passed `SCAN_PROFILE` gates and `wide` is allowed live, do not invent a separate sub-$50k TVL or first-live-tick hold rule.
- [2026-06-24 07:33] send_notification fails with CONDOR_CHAT_ID not configured, so Telegram alerts are unavailable until chat routing is set.

[CURRENT STATUS]
Last tick: #1 at 12:45 UTC
Status: Running | PnL: $+0.00 | Open: 0 executors
Last action: Model metadata for `gpt-5.6-sol` not found. Defaulting to fallback metadata; this can degrade perfor

</details>

## Executor State
Active Executors: none running (agent: lpmaxxing.orca_1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

## Risk State
- Position Size: $0.00 / $10.00 limit
- Open Executors: 0 / 1 limit
- Drawdown: 0.0% / 5.0% limit
- Status: ACTIVE

## Agent Response
Model metadata for `gpt-5.6-sol` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.

## Tool Calls (0)

No tool calls.

## Stats
Duration: 5.1s

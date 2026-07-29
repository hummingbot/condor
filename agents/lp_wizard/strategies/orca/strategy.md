---
name: orca
description: Multi-slot Orca CLMM LP strategy using ranked scanner evidence and guarded local mutations.
agent_key: null
skills: []
default_config:
  account_name: master_account
  risk_profile: yield_focused
  total_amount_quote: 20
  max_slot_count: 2
  min_capital_per_slot_quote: 2
  max_capital_per_slot_quote: 10
  max_slots_per_pool: 1
  max_slippage_pct: 1
  min_sol_reserve: 0.05
  inventory_dust_quote: 0.01
  session_max_age_minutes: 1440
  session_take_profit_net_pnl_ratio: 0.02
  session_stop_loss_net_pnl_ratio: 0.02
default_trading_context: ''
created_by: 0
created_at: '2026-07-28T00:00:00Z'
---

# Orca LP Strategy

Strategy key: `lp_wizard.orca`. The intended live mode is `loop`. Local `config.yml` supplies the runtime configuration; its defaults are mirrored above for Condor's normal strategy config loader.

## Routine-Only Execution

- Use only this agent's `scan`, `state`, `open`, `close`, and `recover` routines with `strategy_id: lp_wizard.orca` and the current controller ID.
- Every routine run attempts to persist a sanitized report with Input, Output, Errors, and Debug sections. Preserve the returned `report_id`; if it is null, surface `report_error` without changing or retrying the reported trading outcome.
- Never call `consult` or `delegate` from this strategy. A routine failure must be handled from its report with `state`, `recover`, or HOLD, never by spawning another ACP session.
- Never invoke global or other-agent routines, pass another outer strategy ID, or supply another controller ID.
- Hummingbot direct tools are observation-only. Never directly create or stop executors, swap, or mutate Gateway resources.
- Override generic Condor direct-create/direct-retry guidance: use `open`, `close`, and `recover` for every live mutation. Submit no direct retry; uncertain calls require `recover`.
- This is agent policy only, not a claim of hard ACP confinement.

## Manual Reports

- `scan` defaults to the read-only `lp_wizard.orca_e1` experiment controller and can be run manually to fetch and rank current Orca pools. Its `risk_profile` is a routine parameter, defaults to `yield_focused`, and never inherits the profile from `config.yml`.
- Running `state`, `open`, `close`, or `recover` with omitted runtime fields creates a sample-only report describing required inputs, execution stages, side effects, and possible outcomes. It does not resolve a session, acquire a lock, contact an API, or mutate state.
- Complete runtime inputs retain normal behavior. Invalid supplied values are errors, not sample requests.

## Tick Policy

- Call `state` first on every tick. It reports this session's slots, capacity, ownership contradictions, and global session-stop advisory. Persisted state from another session never blocks this session.
- `state.session_stop` tracks session age and cumulative controller executor PnL against the immutable session budget. Ratios are decimal ratios, so `0.02` means 2%. If `must_take_action` is true, obey `command` immediately. A reached limit requires an action journal entry prefixed `GLOBAL_SESSION_STOP_LATCHED`, no more scans or opens, recovery of unresolved mutations, occupied-slot closes one at a time with `state` between actions, and a permanently flat remainder of the session; refresh that marker on later ticks so it stays in recent journal context. Incomplete stop evidence requires no new opening until a later `state` makes evaluation complete. This is mandatory LLM policy but is not code-enforced by `open` or `close`.
- Call `scan` with the intended `risk_profile` for ranked, selection-neutral evidence. Choose any eligible candidate and any returned range option, including one below rank 1, or choose no trade.
- An `open` request requires the exact candidate object and option from a fresh scan, the current live controller ID, an empty slot, and a valid amount. Reads from Hummingbot may inform reasoning but never replace scanner eligibility.
- IDs ending in `_eN` are read-only: call only `scan` or `state`. Do not call `open`, `close`, or `recover`; live `run_once` therefore cannot mutate. Use `loop` for live management.
- Manage sessions and slots independently. Another session may use the same wallet and pool. Within this session, do not open a pool already used or pending in another slot, and do not change another slot while operating one slot.
- On uncertainty, call `recover` for the exact controller and slot. Never repeat the uncertain external request.

## Close Policy

- `close` accepts only the current controller ID, slot ID, and optional reason. Do not provide an executor ID, wallet, connector, path, or position identity.
- Executor creation and local stop use `keep_position: true`. This still removes the exact CLMM liquidity position; it only disables Hummingbot's internal close-out swap.
- A terminal receipt requires `COMPLETE`, a cleared executor position address, the exact persisted on-chain position absent, finite nonnegative `base_amount`, `base_fee`, `quote_amount`, and `quote_fee`, and a positive returned base or quote total. Record returned quote principal and fees without swapping them. Sell exactly returned base principal plus base-denominated fees to USDC through the slot's one persisted Jupiter restoration, after verifying available base covers that amount; dust may clear without a swap. Never use wallet deltas or total wallet base.
- A local pending stop or proven native `POSITION_HOLD` may settle. External or ambiguous `EARLY_STOP` blocks. Material restoration follows persisted `intent`, `submitted`, and `uncertain` recovery without blind resubmission.
- Direct Jupiter restoration may leave Hummingbot's virtual `POSITION_HOLD` accounting stale. For the V3 canary, slot state and actual wallet balances are authoritative; report the virtual hold as a warning.
- A confirmed close changes only its selected slot. Leave all other slots unchanged.

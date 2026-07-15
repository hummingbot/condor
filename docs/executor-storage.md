# How Condor stores executors

An **executor** is a declarative trade intent run by a deterministic state
machine against a venue. Every executor has a composite `type` =
**`{kind}_{instrument}`** plus a `venue`:

- **kind** ∈ `order` (single leg — place one order, track to filled/cancelled,
  terminal) | `position` (round-trip — enter, run a SL→trailing→TP→time_limit
  barrier ladder, close).
- **instrument** ∈ `spot` | `perp` | `pred`.
- **venue** ∈ `solana` | `hyperliquid` | `polymarket`.

So there are six types: `order_spot`, `order_perp`, `order_pred`,
`position_spot`, `position_perp`, `position_pred`. The **kind** picks the
executor class (`OrderExecutor` / `PositionExecutor`); the **(instrument,
venue)** pair picks the connector + an **instrument adapter** that wraps it:

| instrument \ venue | `solana` | `hyperliquid` | `polymarket` |
|---|---|---|---|
| `spot` | Jupiter (native Solana) | HL **spot** | — |
| `perp` | — | HL **perp** | — |
| `pred` | — | HL **HIP-4 outcome** | Polymarket CLOB |

The two executor classes are **generic**; all venue specifics live in an
`InstrumentAdapter` (`condor/executors/adapters.py`): `SpotAdapter`,
`PerpAdapter`, `PolymarketPredAdapter`, `HyperliquidPredAdapter`, built by
`make_adapter(instrument, venue, connector, cfg)`. `SpotAdapter` serves both
`solana` and `hyperliquid` spot (same swap interface).

This doc describes where an executor's state lives, how it survives a restart,
and exactly what each type stores. There is **no database** — Condor is
file-based, and executors are no exception.

Both classes share one contract (`ExecutorBase`): a `control_task()`
state-machine tick, a persisted pydantic `state` model, a `_recovery_key()` for
log dedup, `early_stop(keep_position)`, `custom_info()` for reporting,
`net_pnl_quote()`, and `notify_trade` on open/close. They map their internal
states onto one coarse lifecycle — `PENDING → ACTIVE → CLOSING → CLOSED`, or
`→ FAILED` — so the runtime, reconcile, and performance code treat every type
uniformly.

## Three layers

| Layer | Where | Role |
|---|---|---|
| **Live** | the daemon's RAM | The barrier loop (TP / SL / time-limit) runs here at machine speed. Nothing is persisted per tick. |
| **Durable** | `agents/{slug}/executors.jsonl` | Append-only transaction log — the record that rebuilds the open set after a restart and feeds performance. |
| **Truth** | the venue / on-chain | Actual holdings and fills (Solana RPC, Hyperliquid, Polymarket). Wins on any disagreement during reconciliation. |

The chain is **not** the live store (it has no barriers, no phase, no
attribution, and is too slow for a machine-speed stop) and the log is **not** the
live store either (it is written only on transitions). Live management is RAM;
the log is what makes a killed process recoverable; the chain is the final word.

## The transaction log

One file per agent slug, one JSON object per **lifecycle event**, append-only:

```
agents/{slug}/executors.jsonl
```

Executors created without an agent (CLI / chat / manual) go to
`agents/_manual/executors.jsonl`.

Each line is an event — `opened` (carries the full barrier `config` + the open
`state` snapshot), `closed` / `failed` (terminal, carries the final state +
reason), or a plain non-terminal `update`:

```jsonc
{"ts": 1784058592.4, "event": "opened", "id": "position_spot_1784058592_6879c7",
 "type": "position_spot", "agent_slug": "memecoin_trender",
 "agent_id": "memecoin_trender_23", "strategy": "trend_position",
 "status": "ACTIVE", "close_reason": null,
 "config": { /* base/quote token, amount_quote, take_profit_pct, stop_loss_pct,
              time_limit_s, slippage — the barriers */ },
 "state":  { /* entry_price, size, amount_spent, open_ref, opened_at,
               extra: {tx_fee} */ }}

{"ts": 1784058629.9, "event": "closed", "id": "position_spot_1784058592_6879c7",
 "status": "CLOSED", "close_reason": "time_limit hit (pnl -1.47%)",
 "state": { /* exit_price, proceeds, close_ref, close_type, ... */ }}
```

`config` and `state` are **opaque per-type JSON** (each config + the generic
state model serialize their own pydantic models). Instrument-specific persisted
fields go in `state.extra` (a free dict) — the adapter reads/writes it — so the
generic `PositionState`/`OrderState` never grow a per-instrument column.

### Reads are a fold

A record is reconstructed by folding an id's events in file order: `config` from
the opener (immutable intent), `state` / `status` / `close_reason` last-wins,
`created_at` = first event's ts, `updated_at` = last. `ExecutorLog` exposes the
same read surface the code relies on:

- `load(id)` — one executor (fan-out across agent files)
- `load_non_terminal()` — the open set across all agents (used by boot reconcile)
- `load_by_agent(agent_id)` / `load_by_slug(slug)` — one run / one agent's history
- `list_all(limit)` — everything (rare; dashboard/CLI)

### Writes are deduped (no per-tick flooding)

`ExecutorBase.persist()` writes only when the **recovery-relevant** snapshot
changes — a per-type *recovery key* of `(status, close_reason, phase, tx ids)`,
deliberately excluding volatile price/pnl fields. A position held for hundreds of
poll iterations therefore appends **~one line per phase transition**
(opened → active → closing → closed), not one line per poll. The intent is
recorded *before* the open tx is sent (`submitting` / `OPENING`), so a landed tx
always has a prior log line.

## What each executor type stores

Every event carries the type's `config` (immutable intent, from the opener) and a
`state` snapshot (last-wins). All configs share the **base fields** from
`ExecutorConfig`: `type`, `chain_network`, `wallet_address`, `agent_slug`,
`agent_id`, `strategy`, `user_id`, `chat_id`, `notify_trades`, `update_interval`,
`max_retries`. Below, only the **type-specific** fields are listed. The
**recovery key** is the dedup tuple (combined with `status` + `close_reason`): the
log appends a line only when it changes.

All barrier fields (`take_profit_pct`, `stop_loss_pct`, `time_limit_s`,
`trailing_activation_pct`, `trailing_delta_pct`) come from the shared
`BarrierFields` mixin on every `position_*` config. Venue defaults per
instrument: `spot`→`solana`, `perp`→`hyperliquid`, `pred`→`polymarket`;
`chain_network`/`wallet_address` default to `""` for hyperliquid/polymarket.

#### `OrderState` (kind = order)
- **states**: `NOT_ACTIVE → SUBMITTING → (RESTING) → DONE | FAILED`
- **state**: `state`, `size`, `entry_price`, `open_ref`, `close_type`, `extra`
- **recovery key**: `(state, open_ref)`

#### `PositionState` (kind = position)
- **states**: `NOT_ACTIVE → OPENING → ACTIVE → CLOSING → COMPLETE | FAILED`
- **state**: `state`, `opened_at`, `entry_price`, `size`, `amount_spent`,
  `mark_price`, `pnl_pct`, `trailing_trigger_pct`, `exit_price`, `proceeds`,
  `open_ref`, `close_ref`, `close_type`, `extra`
- **recovery key**: `(state, open_ref, close_ref, *adapter.recovery_ids)` — the
  adapter contributes any durable ids (perp: `tp_oid`, `sl_oid`) so native TP/SL
  triggers survive a restart

### order_spot — one-way swap
- **config**: `base_token`, `quote_token`, `amount`, `side`, `slippage_pct`,
  `order_type`, `limit_px`, `notional_quote`
- **state.extra**: `amount_in`, `amount_out`, `fee`

### order_perp / order_pred — single-leg perp / outcome entry
- **order_perp config**: `coin`, `side`, `notional_quote`, `leverage`,
  `cross_margin`, `slippage_pct`, `order_type`, `limit_px`
- **order_pred config**: `market`, `position`, `amount_quote`, `slippage_pct`,
  `order_type`, `limit_px`

### position_spot — spot triple-barrier (TP / SL / TTL / trailing)
- **config**: `base_token`, `quote_token`, `amount_quote`, `slippage_pct` + barriers
- **state.extra**: `tx_fee` (native, cumulative)

### position_perp — leveraged perpetual (Hyperliquid) with liquidation guard
- **config**: `coin`, `side`, `notional_quote`, `leverage`, `cross_margin`,
  `entry`, `limit_px`, `slippage_pct`, `liquidation_guard_pct`,
  `native_triggers` + barriers
- **state.extra**: `leverage`, `tp_oid`, `sl_oid`, `liquidation_px`,
  `unrealized_pnl`, `realized_pnl`, `close_fee` (`open_ref`=entry oid,
  `close_ref`=close oid)

### position_pred — outcome market (Polymarket / Hyperliquid HIP-4)
- **config**: `venue` (`polymarket` | `hyperliquid`), `market` (token_id or
  outcome id/name), `position` (LONG=Yes / SHORT=No), `amount_quote`,
  `slippage_pct`, `resolve_win_price`, `resolve_loss_price` + barriers
- **state.extra**: — (none; proceeds/mark carry the accounting)

Prices/pnl (`mark_price`, `pnl_pct`, and the `extra` unrealized fields) are
deliberately **excluded** from every recovery key — they change each tick, and
persisting them would defeat the dedup. They're still written whenever a
recovery-relevant field changes; the log is for recovery, not a price feed.

## Recovery on restart

Barriers live in the process, so a restart must re-adopt open positions:

1. On boot, `start_executor_service()` calls `ExecutorRuntime.reconcile()`.
2. `load_non_terminal()` replays every `executors.jsonl` → the open set, each
   carrying its logged barriers + `open_ref`.
3. Reconcile is generic, keyed on **kind**:
   - **position**: `adapter.held_size() > 0` → rebuild from the logged
     config/state and resume the RAM barrier loop (TTL from `opened_at`, SL/TP
     from `entry_price`); mid-`CLOSING` → re-adopt and let the close retry;
     otherwise settle. The venue is the truth.
   - **order**: a resting/in-flight ref → re-adopt and let `poll()` drive it
     terminal; `SUBMITTING` with no ref → orphan (FAILED).
4. A watchdog flattens any executor whose task died or stalled, using an
   in-memory liveness timestamp (not the log, which no longer heartbeats). Only a
   `position` holds an open leg to flatten (`adapter.close(size)`); an `order` is
   a single leg with nothing to unwind.

## Who owns the runtime

The `ExecutorRuntime` — the sole writer of the logs — lives in **one persistent
process**, either:

- `python -m condor.daemon` — headless: runtime + control socket, no web app; or
- `main.py` — the web app, which starts the identical runtime + control socket in
  its lifespan and also serves the dashboard.

Run **one**, never both (they share the runtime, the logs, and the single
control socket). A harness-spawned MCP subprocess (or the dashboard) reaches the
runtime over the unix control socket (`condor/control/`), which dispatches to the
transport-agnostic operations in `condor/executors/ops.py`. Everyone else only
**reads** the logs; single-writer discipline is what keeps append-only safe.

## Map of the code

| Concern | Module |
|---|---|
| Transaction log (read/write/fold) | `condor/executors/log.py` |
| Record shape + slug helper | `condor/executors/records.py` |
| Runtime: create / stop / reconcile / watchdog | `condor/executors/runtime.py` |
| Base state machine + persist dedup + `BarrierFields` | `condor/executors/base.py` |
| Order executor + 3 order configs + `OrderState` | `condor/executors/order.py` |
| Position executor + 3 position configs + `PositionState` | `condor/executors/position.py` |
| Instrument adapters + `make_adapter` | `condor/executors/adapters.py` |
| Connectors | `condor/executors/{solana,jupiter,hyperliquid,hyperliquid_spot,hyperliquid_outcome,polymarket}.py` |
| Venue credential loading | `condor/executors/wallets.py` |
| Transport-agnostic ops | `condor/executors/ops.py` |
| Performance / scorecard (folds closed events) | `condor/executors/performance.py` |
| Runtime singleton + startup | `condor/executors/service.py` |
| Control socket (server/client/handlers) | `condor/control/` |
| Headless host | `condor/daemon.py` |

## What there is *not*

- **No `condor.db`** and no SQLite `ExecutorStore` — removed. Executor state is
  the per-agent log; the chain is the reconciliation truth.
- **No per-tick persistence** — only lifecycle transitions are logged.
- **A DB would only be justified** as a read-only projection *rebuilt from the
  logs* if reporting ever outgrows on-demand file scans — never as the write path.

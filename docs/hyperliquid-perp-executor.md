# Hyperliquid perp executor — spec

Adds leveraged perpetual trading on **Hyperliquid** as a native Condor executor.
Because Hyperliquid is a **non-custodial, key-signing perp DEX**, this is the same
shape as the existing Gateway swap/position/lp executors — a thin venue client
plus an executor type — and reuses the transaction log, reconcile, barriers, and
control socket unchanged. **It lives in the `condor` repo** (`condor/executors/`);
no separate service or credential backend is needed.

**One unified connector.** Unlike hummingbot — which ships two connectors,
`hyperliquid` (spot) and `hyperliquid_perpetual` — Condor registers a **single
`hyperliquid` venue** that trades both perps and spot under one agent key and one
credential entry. Hyperliquid is one account and one signing scheme across
products, so the split buys nothing here; the perp executor is the first consumer,
and spot swaps can reuse the same client later without a second connector.

## Auth model — trade-only agent wallet

Hyperliquid lets the main account **approve an "agent wallet"** (a separate
private key) that can **place and cancel orders but cannot withdraw or transfer**.
Condor holds only that agent key:

- **Stored** in Condor's encrypted config (`config_manager`, one "hyperliquid"
  venue entry — spot and perp share it): `agent_private_key` + the public
  `account_address` (main account the agent trades for) + `network` (mainnet |
  testnet).
- **Approving** the agent + the 1 bps builder code is the existing web-dashboard
  flow (`ConnectHyperliquid`, `frontend/src/lib/wallet/hyperliquid.ts`): the
  browser generates the agent key, the user signs `ApproveAgent` and
  `ApproveBuilderFee` with their own wallet, and only the agent key reaches Condor.
  Condor never sees the withdrawal key.
- Worst case if the agent key leaks: bad trades, **not** drained funds. This is
  why keys-in-Condor is acceptable here (unlike CEX API keys).

## Builder code — 1 bps, opt-in (mirrors `~/hummingbot`)

Reuse the **same Foundation builder code** as hummingbot's Hyperliquid connectors
(`hyperliquid_perpetual_constants.py`): builder address
`0x10BA451e6439Efc6a17dc20d21121Aa838100705`, fee
`FOUNDATION_BUILDER_FEE_TENTHS_BPS = 10` (tenths of a bp = **1 bps = 0.01%**). The
fee supports Condor's maintenance via the not-for-profit Hummingbot Foundation.

It is **opt-in and self-limiting**, exactly as in hummingbot:

- The dashboard flow above has the user sign `ApproveBuilderFee` for this builder
  at a max rate of 1 bps. Nothing is charged unless they approve.
- **Resolve the effective fee once at client startup** (mirrors
  `_initialize_builder_fee`): query the `maxBuilderFee` info endpoint for
  `(account_address, builder)` and set the per-order fee to
  `min(approved_max_tenths_bps, 10)` — so 1 bps if approved, **0 if not** (or if
  the lookup fails; log and charge 0 that session).
- **Attach the builder field to every order action** (mirrors
  `_build_builder_field`): `{"b": <builder_address_lowercased>, "f":
  <fee_tenths_bps>}`. Address must be lowercased; the venue rejects mixed-case.
- **Mainnet, non-vault only** (mirrors `_should_inject_builder`): the venue
  rejects the builder field on testnet and vault orders — omit it there.

The cap is on-chain: even if the fee constant were raised, the venue never charges
more than the user's approved max, so 1 bps is the ceiling users authorize.

## `PerpDexClient` — venue-agnostic interface

Mirrors `GatewayClient`'s role. Kept venue-agnostic so Lighter (and others) drop
in behind the same interface later.

```python
class PerpDexClient(ABC):
    async def set_leverage(self, coin: str, leverage: int, cross: bool = True) -> None: ...
    async def market_open(self, coin: str, is_buy: bool, size: Decimal,
                          slippage_pct: Decimal) -> Fill: ...
    async def place_limit(self, coin: str, is_buy: bool, size: Decimal,
                          price: Decimal, tif: str = "Gtc",
                          reduce_only: bool = False) -> OrderAck: ...
    async def place_trigger(self, coin: str, is_buy: bool, size: Decimal,
                            trigger_px: Decimal, kind: str,  # "tp" | "sl"
                            reduce_only: bool = True) -> OrderAck: ...
    async def market_close(self, coin: str, size: Decimal | None = None) -> Fill: ...
    async def cancel(self, coin: str, oid: int) -> None: ...
    async def order_status(self, coin: str, oid: int) -> OrderStatus: ...
    async def position(self, coin: str) -> Position | None: ...   # None if flat
    async def fills(self, since_ms: int | None = None) -> list[FillRecord]: ...
    async def mid_price(self, coin: str) -> Decimal: ...
    async def account(self) -> AccountSummary: ...                # margin, withdrawable

# Position: {coin, side, size, entry_px, unrealized_pnl, liquidation_px,
#            margin_used, leverage, funding}
```

### `HyperliquidClient(PerpDexClient)`

Wraps the official `hyperliquid-python-sdk`:

| Interface method | SDK call |
|---|---|
| `set_leverage` | `Exchange.update_leverage(leverage, coin, is_cross)` |
| `market_open` | `Exchange.market_open(coin, is_buy, sz, None, slippage)` |
| `place_limit` | `Exchange.order(coin, is_buy, sz, px, {"limit": {"tif": tif}}, reduce_only)` |
| `place_trigger` | `Exchange.order(..., {"trigger": {"triggerPx", "isMarket": True, "tpsl": kind}}, reduce_only=True)` |
| `market_close` | `Exchange.market_close(coin)` |
| `cancel` | `Exchange.cancel(coin, oid)` |
| `position` | `Info.user_state(address).assetPositions[coin].position` (szi signed, entryPx, unrealizedPnl, liquidationPx, marginUsed) |
| `order_status` | `Info.query_order_by_oid` / `Info.open_orders` |
| `fills` | `Info.user_fills(address)` |
| `mid_price` | `Info.all_mids()[coin]` |
| `account` | `Info.user_state(address).marginSummary` |

`Exchange` is constructed with the agent `LocalAccount` + `account_address` +
`base_url` (mainnet/testnet). Sizes/px respect the per-coin decimals from
`Info.meta()`. On construction the client resolves the builder fee once (see
Builder code) and passes the `{"b","f"}` builder field on every order-placing SDK
call (`order` / `market_open` accept a `builder=` argument); trigger and cancel
actions carry no builder field.

## Config / state / states

```python
class PerpStates(str, Enum):
    NOT_ACTIVE, OPENING, ACTIVE, CLOSING, COMPLETE, FAILED

class PerpConfig(ExecutorConfig):
    type: Literal["perp"] = "perp"
    venue: str = "hyperliquid"
    coin: str                          # "ETH", "BTC", "SOL", ...
    side: Literal["LONG", "SHORT"]
    notional_quote: Decimal            # USD notional; size = notional/entry
    leverage: int = 1
    cross_margin: bool = True
    entry: Literal["market", "limit"] = "market"
    limit_px: Optional[Decimal] = None # required when entry="limit"
    slippage_pct: Decimal = Decimal("0.5")
    # Barriers (fractions of the entry PRICE move, hummingbot semantics)
    take_profit_pct: Optional[Decimal] = None
    stop_loss_pct: Optional[Decimal] = None
    time_limit_s: Optional[int] = None
    trailing_activation_pct: Optional[Decimal] = None
    trailing_delta_pct: Optional[Decimal] = None
    # NEW perp-only barrier: close if mark within this % of liquidation price
    liquidation_guard_pct: Decimal = Decimal("0.15")
    # Also place native reduce-only TP/SL triggers on HL as a daemon-down backstop
    native_triggers: bool = True

class PerpState(BaseModel):
    state: PerpStates = NOT_ACTIVE
    opened_at: Optional[float]
    side: Optional[str]
    size: Decimal = 0                  # base (coin) units actually filled
    entry_px: Optional[Decimal]
    leverage: Optional[int]
    entry_oid: Optional[int]
    tp_oid: Optional[int]; sl_oid: Optional[int]
    close_oid: Optional[int]
    mark_px: Optional[Decimal]; pnl_pct: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]; liquidation_px: Optional[Decimal]
    trailing_trigger_pct: Optional[Decimal]
    realized_pnl: Optional[Decimal]; exit_px: Optional[Decimal]
    open_fee: Decimal = 0; close_fee: Decimal = 0; funding_paid: Decimal = 0
    close_type: Optional[str]          # take_profit|stop_loss|time_limit|trailing_stop|liquidation_guard|liquidated|early_stop|detached
```

`pnl_pct` = price-move return, sign-adjusted: LONG `(mark-entry)/entry`, SHORT
`(entry-mark)/entry`. (ROE = `pnl_pct * leverage`; barriers are on price-move for
parity with the spot executors — see Open decisions.)

## Executor lifecycle

**`_open`** (NOT_ACTIVE → OPENING → ACTIVE)
1. `set_leverage(coin, leverage, cross)`.
2. Persist **OPENING intent before the order** (option-A recovery: a landed order
   always has a prior log line).
3. `size = notional_quote / mark`; place entry (`market_open` IOC, or `place_limit`).
4. Poll `order_status` until filled (accumulate partial fills) → `entry_px` (avg),
   `size`, `open_fee`, `opened_at`, `liquidation_px` (from `position`).
5. If `native_triggers`: place reduce-only **TP** and **SL** trigger orders on HL
   (backstop that survives a daemon outage); record `tp_oid`/`sl_oid`.
6. → ACTIVE, notify 🟢.

**`_control_barriers`** each tick — priority order (liquidation first):
1. Fetch `position` (or `mid_price`); recompute `mark_px`, `pnl_pct`,
   `unrealized_pnl`, `liquidation_px`.
2. **Position vanished** (native trigger fired, or liquidated externally) → settle
   from `fills`, cancel leftover triggers, → COMPLETE.
3. **Liquidation guard**: mark within `liquidation_guard_pct` of `liquidation_px`
   → close now (highest priority — never rely on the exchange liquidating you).
4. **Stop loss** → close. 5. **Trailing** → close. 6. **Take profit** → close.
7. **Time limit** (`opened_at + time_limit_s`) → close — enforced even if the
   price fetch failed.

**`_close`** (CLOSING → COMPLETE)
1. Cancel `tp_oid`/`sl_oid` if present.
2. `market_close(coin, size)` (reduce-only); poll until flat → `exit_px`,
   `realized_pnl`, `close_fee`, `close_type`.
3. → COMPLETE, notify 🔴.

**`early_stop(keep_position)`**: `False` → `market_close` now (close_type
`early_stop`); `True` → **detach**: leave the position (and its native triggers)
on HL, stop managing (close_type `detached`).

### Native triggers vs local barriers — the hybrid is the point

Local polling gives TTL, trailing, the liquidation guard, and fast reaction.
Native reduce-only TP/SL triggers on HL give a **backstop that fires even if the
Condor daemon is down** — directly mitigating the "barriers die with the process"
caveat. We run both: the daemon manages normally; if it dies, the venue still
holds the stop. On restart, reconcile re-adopts and re-syncs the triggers.

## Recovery key + reconcile

`_recovery_key()` → `(state.value, entry_oid, tp_oid, sl_oid, close_oid)` —
durable identifiers only, so the dedup'd log appends ~one line per transition.

`_reconcile_one` for `perp`:
- **OPENING**: `order_status(entry_oid)` filled → resume ACTIVE; resting/unfilled →
  cancel + retry-or-fail; no oid but `position(coin)` exists → orphan, adopt/flag.
- **ACTIVE**: `position(coin)` present & matches → re-adopt, resume the loop,
  re-place any missing native triggers; **absent** → closed/liquidated externally →
  settle from `fills`, append `closed`.
- **CLOSING**: retry `market_close` (an already-flat position fails loudly).
- Venue/on-chain state wins on every disagreement (same rule as the DEX path).

## Runtime wiring

- Register `"perp"` in `runtime._EXECUTOR_TYPES` → `(PerpConfig, PerpExecutor)`.
- **Connector resolution**: `create_executor`/`reconcile` currently inject
  `self.gateway`. Generalize to pick the connector by type — `GatewayClient` for
  swap/lp/position, a `HyperliquidClient` (built from the venue's stored creds) for
  perp. Small change: a `connector_for(config)` helper on the runtime; the
  executor's `__init__` takes a connector in the existing `gateway` slot.
- **Risk declaration**: `max_notional_quote = notional_quote`;
  `max_loss_quote = notional_quote / leverage` (margin at risk — the true bound is
  the posted margin to liquidation; TP/SL/liq-guard keep the *intended* loss well
  under it).
- **Storage / control / notifications**: unchanged — logs to
  `agents/{slug}/executors.jsonl`, driven via the control socket's `executor.*`
  ops, `notify_trade` on open/close.

## Dependencies

- Add `hyperliquid-python-sdk` to `pyproject.toml`. It brings `eth_account` for
  signing. This is the executor's only new dep — and it's what lets us **drop
  `hummingbot-api-client` entirely** (perp DEXs need no CEX connectors).

## Testnet validation (mirrors the Gateway swap validation)

1. Approve a testnet agent wallet from a testnet main account; fund testnet USDC.
2. Store creds in config; construct `HyperliquidClient` on `testnet`.
3. Open a tiny ETH perp (e.g. $20 notional, 2x): assert fill, entry_px,
   `liquidation_px`, native TP/SL placed, one `opened` log line.
4. Trip a barrier (tight TTL) → assert `market_close`, realized pnl, `closed` log.
5. Kill mid-position → restart → `reconcile` re-adopts from `position` + log.
6. Verify `manage_executors get/list/performance` over the socket reflect it.

## Open decisions

1. **Barriers on price-move vs ROE.** TP/SL as % of the *price move* (parity with
   spot) or as % *return on margin* (`×leverage`)? Price-move is the default;
   surface a `barriers_on: "price" | "roe"` knob if you want both.
2. **Entry order type default.** Market (IOC, taker fee, certain fill) vs limit
   (maker rebate, may not fill). Default market for momentum; limit optional.
3. **Native triggers on by default?** Recommended `True` (daemon-down backstop),
   at the cost of two extra resting orders per position.
4. **One position per (coin, side)?** HL nets positions per coin — decide whether
   two Condor perp executors on the same coin are allowed (they'd share one
   on-chain position). Likely enforce one executor per coin per account.

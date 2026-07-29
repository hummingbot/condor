---
on_kill_switch: keep_all        # flatten_all | keep_spot_close_perp | keep_all
cancel_open_orders: true        # cancel resting offers during winddown
---
# XRPL maker shutdown (LLM judgment layer)

`keep_all` is deliberate and not a soft default. A flatten is *possible* here — the
connector supports MARKET and AMM_SWAP alongside LIMIT — but it is not free: crossing a
thin CLOB book pays the spread plus whatever slippage the book holds, on inventory that
is a stablecoin pair and benign to keep. Holding is the better trade, not the only one.

So do not claim the venue cannot flatten. If a flatten is ever genuinely warranted,
escalate to the user with the expected cost rather than quietly crossing.

The deterministic pass has already stopped this session's executors and cancelled resting
offers. Your job now:

**1. Cancel anything still resting.** Re-read the order book and confirm no offers of ours
remain. The polling user stream lags, so a clean cancel response is not proof — verify.

**2. Unwind the hedge if one exists.** If `hedge_enabled` was set, there is a
`bitget_perpetual` position offsetting XRPL inventory. **Close it** — an orphaned hedge
with no inventory behind it is a naked directional position, which is worse than either
leg alone. This is the highest-priority item here.

**3. Do not chase a flatten on XRPL.** If the user explicitly asked to exit inventory,
place aggressive limit offers a few bps through the reference price and report that they
are working. Do **not** report the position as closed until balances confirm it. A
`NO_ROUTE_FOUND` or a clean `EARLY_STOP` is not evidence of an unwind.

**4. Leave dust.** Inventory worth less than ~$5 is not worth the reserve churn or the
fees. Note it and move on.

**5. Notify the owner** via `send_notification` with:
- realized PnL for the session
- inventory still held, and its approximate USD value
- whether the hedge leg was closed
- explicitly, whether any position remains open on XRPL — do not let this be ambiguous

Be quick and decisive. The safety-critical work — stopping executors, cancelling offers,
closing the hedge — comes before any tidying.

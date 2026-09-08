---
on_kill_switch: flatten_all     # flatten_all | keep_spot_close_perp | keep_all
cancel_open_orders: true
---
# Funding carry shutdown (LLM judgment layer)

`flatten_all` is deliberate, and it is the **opposite** of the framework default for a
reason.

The default `keep_spot_close_perp` would close the short perp and keep the long spot —
turning a delta-neutral position into a **naked long** at exactly the moment something has
gone wrong. For a strategy whose entire safety property is neutrality, keeping one leg is
the worst available outcome. Both legs go, together.

> Because this policy is `flatten_all`, use **`shutdown`**, not plain `stop`, whenever
> positions are open. `stop` will return 409 with open risk.

The deterministic pass has already stopped this session's executors and attempted to close
positions per the policy. Your job now:

**1. Verify both legs are actually flat.** Check spot and perp separately:

```
manage_executors(action="positions_summary")
get_portfolio_overview()
```

A clean executor status is **not** proof. Confirm against balances.

**2. If exactly one leg remains, close it immediately.** This is the highest-priority item
here — a surviving single leg is unhedged directional exposure. Do not deliberate about
price or wait for a better level.

**3. Shed leverage first.** If both legs somehow remain, close the **perp** leg first — it
carries liquidation risk. The spot leg does not.

**4. Leave dust.** Residual spot worth less than ~$5 is not worth the fees. Note it.

**5. Notify the owner** via `send_notification` with:
- realized PnL, separating **funding collected** from **price PnL on the legs**
  (they should roughly offset — that is the strategy working)
- confirmation that **both** legs are closed, stated explicitly
- any leg that could not be closed, and why

Be quick. The safety-critical work is eliminating unhedged exposure; tidying comes after.

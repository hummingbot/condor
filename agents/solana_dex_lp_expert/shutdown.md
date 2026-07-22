---
on_kill_switch: flatten_all     # close ALL LP positions on shutdown (not keep_spot)
cancel_open_orders: true         # cancel any resting/Gateway orders during winddown
---
# Emergency shutdown — Solana LP Expert

The deterministic winddown has already stopped every one of this session's
`lp_executor`s with `keep_position=false` — which for a CLMM position **removes
the on-chain liquidity and refunds the position rent**. LP positions are treated
as risk to flatten, NOT as spot to keep. You are the best-effort cleanup pass on
top of that guaranteed floor.

> **⚠ THE SWAP-BACK LEG IS NOT GUARANTEED — ALWAYS VERIFY IT YOURSELF.**
> `keep_position=false` is *supposed* to also swap the withdrawn base tokens back
> to the quote asset. **Observed twice (sessions 10 and 11): the liquidity came out
> but the swap-back silently failed**, leaving the full base position sitting as
> spot (BONK 1.74M, PUMP 2588, ANSEM 24.6, JIMOTHY 253 — hundreds of dollars) while
> every `lp_executor` reported a clean `EARLY_STOP`. **A clean executor status is NOT
> evidence the tokens were converted.** Removing liquidity and converting it are two
> separate legs; only the first is reliable.
> **You are not done until the on-chain base-token balance is ~0.** Re-read the
> wallet after the sells and retry anything still held.

Now:

- Verify no LP position is still open: `get_portfolio_overview(include_lp_positions=True)`.
  If any CLMM position for this session is still on-chain, close it — stop its
  executor with `keep_position=false`, and if it has no executor, remove the
  liquidity via the Gateway `/clmm/close` path.
- **Swap the base tokens back — this is mandatory, not a dust sweep.** After the
  liquidity is out, read the **actual on-chain wallet balance** of every base mint
  this session touched (do NOT trust the executor's reported amounts) and sell each
  back to the quote asset with an `order_executor` MARKET **sell** (use the token
  **mint** in the trading pair — Gateway can't resolve memecoins by symbol). Sell
  the *whole* balance, not a dust-sized slice. Only skip a leftover genuinely worth
  under ~$5. **Then re-read the balances and retry any that are still non-zero.**

- **PACE THE SELLS — Jupiter rate-limits, and it lies about why.** When throttled,
  Gateway surfaces the failure as `NO_ROUTE_FOUND` with the giveaway text
  `Unexpected token 'R', "Rate limit"... is not valid JSON`. That is a **rate limit,
  not a missing route** — do NOT conclude the token is unsellable or blacklist it.
  Spacing depends on whether a Jupiter API key is configured (`jupiter.apiKey`):
  - **With a Pro/portal key** (routes via `api.jup.ag`, ~60 req/min): sequential
    sells are fine; leave **~2 s** between them. Observed 0–1 retries per sell.
  - **Without a key** (falls back to `lite-api.jup.ag`, much tighter): leave
    **≥15–20 s** between sells and expect intermittent failures. Observed the same
    sells failing after **10+ retries** purely from throttling.
  If a sell fails this way, wait and retry rather than escalating — it succeeds once
  the window clears.
- Confirm the position **rent** was refunded (each Meteora slot locks ~0.057 SOL).
- Notify the owner via `send_notification` with final realized PnL and a one-line
  summary of the slots wound down.

Be decisive and quick — the safety-critical closes are already done.

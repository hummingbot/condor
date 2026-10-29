---
on_kill_switch: flatten_all     # close ALL LP positions on shutdown (not keep_spot)
cancel_open_orders: true         # sweeps resting CLOB orders; a Gateway LP position has no cancel path
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
> **Read the wallet and report what is still held** — you cannot convert it from
> this seat (see below), so the deliverable is an accurate list, not a zero
> balance. A clean executor status is not evidence of anything about the tokens.

Now:

- Verify no LP position is still open: `get_portfolio_overview(include_lp_positions=True)`.
  If any CLMM position for this session is still on-chain, close it — stop its
  executor with `keep_position=false`, and if it has no executor, remove the
  liquidity via the Gateway `/clmm/close` path.
- **Report the leftover base tokens — you cannot sell them from this seat.** After the
  liquidity is out, read the **actual on-chain wallet balance** of every base mint this
  session touched (do NOT trust the executor's reported amounts) and write down what is
  still held, with its mint and approximate value.

  **Do not try to swap it back.** The swap-back needs a MARKET `create_order_executor`
  sell, and a winddown refuses every create: only stops and liquidity removals pass the
  risk gate once a kill switch has fired, and this seat has no `execute_swap` either.
  Attempting it spends the pass discovering the refusal and changes nothing (LP-5). The
  safety-critical half — removing the liquidity — is already done by the deterministic
  pass above; converting the proceeds is an attended job.

  So: include every non-zero base balance in the `send_notification` below, named by
  mint, and say plainly that it is still held as spot and needs converting by an operator
  or an attended agent. A leftover genuinely worth under ~$5 can be called dust and
  skipped. Journal the list either way, so the next session does not re-discover it.
- Confirm the position **rent** was refunded (each Meteora slot locks ~0.057 SOL).
- Notify the owner via `send_notification` with final realized PnL and a one-line
  summary of the slots wound down.

Be decisive and quick — the safety-critical closes are already done.

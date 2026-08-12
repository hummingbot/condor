---
name: solana_inventory_cleanup
description: Precision-safe Solana residual-inventory cleanup through current-session Order Executors.
when_to_use: Read exactly once when this tick actively starts or reconciles post-close or abandoned-preparation CLEANUP, makes its one corrected cleanup retry, quarantines an exhausted cleanup residual, or completes that manual-recovery handoff. Ordinary close, deployment, supervision, and quiet HOLD do not trigger this skill.
source: agent:lp_agent_lite
---

# Solana Inventory Cleanup

This is the cleanup playbook for Solana token inventory regardless of the LP
venue that produced it. The Strategy owns phase order and admission. Use this
skill only after a terminal close or abandoned confirmed preparation has made
cleanup active; never use it to inspect or repair an on-chain LP position.

Load it once for the active cleanup work and keep the tick narrow: reuse the
canonical wallet, executor, metrics, pair, mint, decimals, and prior cleanup
evidence. Do not select a pool, register a token, rescan markets, load another
skill, or perform unrelated deployment work.

## Establish Exact Cleanup Evidence

Require all of the following before a cleanup mutation:

- exact current controller and configured account/network/QUOTE identity;
- exact BASE symbol, on-chain mint, decimals, and canonical `BASE-QUOTE` pair;
- a fresh wallet balance for that exact mint and its reported precision;
- exact current-controller reconciliation of any earlier cleanup executor;
- value above `residual_base_dust_quote`, unless SOL handling below says no sale;
- one concise cleanup intent, followed by the normal liveness check.

In `snapshot_lp_metrics.residuals`, `mint` is always the exact on-chain Solana
mint address, never a ticker or display symbol. A precision failure does not
change token identity and never creates a blacklist record.

## Construct A Spendable Amount

Never request the full rounded display balance. Compute with exact decimal or
integer arithmetic, never binary floating point.

For a non-SOL token:

1. If authoritative raw token units are available, use
   `safe_raw = max(raw_balance - 1, 0)` and
   `safe_amount = safe_raw / 10^base_decimals`.
2. Otherwise derive one display quantum from the fresh balance string. For a
   plain value with `d` displayed decimal places, `quantum = 10^-d`; for a `K`
   or `M` suffix, scale that quantum by `1,000` or `1,000,000`. Use
   `safe_amount = max(display_balance - quantum, 0)`.
3. Floor the result to accepted BASE precision, then value that safe amount in
   QUOTE using the fresh positive QUOTE-per-BASE price already present in the
   cleanup evidence. If it is zero or its QUOTE value is not strictly above
   `residual_base_dust_quote`, leave the remainder as dust and do not mutate.

Examples: displayed `512.0476` becomes `512.0475`; displayed `9.5398` becomes
`9.5397`. This deliberate one-quantum remainder avoids a debit at the rounded
wallet boundary. It is not a dust top-up or an estimate of swap slippage.

For SOL, retain the protected reserve. Only after a terminal SOL-base LP close,
and only while configured QUOTE is below `total_amount_quote`, use a fresh
positive QUOTE-per-SOL price already present in current evidence, or one exact
native price read for the known pool when absent:

```text
protected_sol = min_sol_reserve * (1 + capital_headroom_pct / 100)
excess_sol = max(fresh_sol_balance - protected_sol, 0)
quote_shortfall = max(total_amount_quote - fresh_quote_balance, 0)
sol_to_sell = min(excess_sol, quote_shortfall / sol_price_quote)
```

Floor `sol_to_sell` to accepted precision and leave SOL when QUOTE is already at
target or no protected excess exists. Never turn this into a broad pool scan.

## Submit Only An Order Executor

Use only `manage_executors(action="create", executor_type="order_executor")`.
The request contains:

- top level: exact current `controller_id`,
  `account_name=<config.account_name>`, and `executor_type="order_executor"`;
- config: `type="order_executor"`, `connector_name=<config.network>`, exact
  canonical `trading_pair`, `side=2`, the precision-safe BASE `amount`, and
  `execution_strategy="MARKET"`.

Do not add provider, slippage, quote, pool, mint, or invented fields. Never call
`manage_gateway_swaps`, request a Gateway quote, register the token, or use a
direct swap tool. Trust Gateway/Jupiter's internal slippage protection. A create
receipt is `submitted`; retain its full executor ID and end without post-create
reads.

## Reconcile And Correct Once

On a later tick, reconcile the exact cleanup executor, then refresh the exact
mint wallet balance. A successful terminal cleanup is measured from the wallet,
not from `executed_amount_base`, which may repeat the requested amount.

A failed cleanup is `rejected_before_submit` only when authoritative evidence
proves the exact executor terminal/failed with zero execution or transaction
effect and the refreshed exact-mint wallet balance unchanged. Jupiter
`6024`/`0x1788` (`InsufficientFunds`) with a successful quote is one known form
of this zero-effect precision-boundary failure. Any transaction identity,
partial fill, balance change, timeout, missing exact detail, or contradiction is
`uncertain` and forbids retry.

After proven `rejected_before_submit`, allow one corrected cleanup on a later
tick. Recompute the safe amount from the refreshed balance, then require:

```text
corrected_amount = min(refreshed_safe_amount,
                       prior_requested_amount - one_current_quantum)
```

Floor again and submit only when positive, above dust, and strictly smaller than
the prior request. Never repeat an identical amount. One corrected cleanup is
the maximum; another zero-effect failure quarantines only that exact residual
mint and amount.

## Quarantine Without Stalling The Session

An exhausted or uncertain cleanup quarantines only the affected residual
inventory and any capacity that truly depends on it. It does not block healthy
LP supervision/exits, cleanup of another token, or independently free capacity.
Preserve exact mint, symbol, amount, QUOTE value, failed executor IDs, and
outcome; never claim the wallet is quote-clean.

Never blacklist a token or pool because cleanup sizing, precision, Jupiter
`6024`, or an executor failed. The only blacklist remains the Strategy's exactly
proven preparation receive-difference threshold.

If wind-down has all current-session LP and Order Executors terminal, no
submitted or uncertain mutation remains, the one corrected cleanup is
exhausted, and only explicitly quarantined residual inventory remains, do not
repeat `HOLD` forever. Journal the exact action `STOP_MANUAL_RECOVERY` with the
residual and failed-executor facts, then call
`manage_trading_agent(action="stop_agent", agent_id=<exact current>)`. This is a
manual-recovery handoff, not successful cleanup.

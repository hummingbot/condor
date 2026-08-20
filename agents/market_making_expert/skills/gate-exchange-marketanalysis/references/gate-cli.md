---
name: gate-exchange-marketanalysis-gate-cli
version: "2026.3.30-1"
updated: "2026-03-30"
description: "gate-cli execution specification for market analysis scenarios including liquidity, momentum, liquidation, basis, and slippage simulation."
---

# Gate Market Analysis MCP Specification

## 1. Scope and Trigger Boundaries

In scope:
- Read-only market analysis for spot/futures
- Scenario-based analysis in `references/scenarios.md` (Cases 1-13)

Out of scope:
- Any order placement, cancellation, leverage, transfer, or fund movement

Misroute examples:
- If user asks to execute trades, route to execution skills (spot/futures/copilot).

## 2. `gate-cli` detection and Fallback

Detection:
1. Verify read-only market data tool families are available.
2. Probe with the smallest required endpoint for selected market type.

Fallback:
- If futures data endpoints are unavailable, provide spot-only analysis and disclose limitation.
- If both spot/futures endpoints are unavailable, return framework-level reasoning only and mark as data-unavailable.

## 2.1 `gate-cli cex …` execution flow (MUST)

For every documented **`gate-cli cex …`** leaf command, **strictly** follow this order:

1. **Preflight with `--help`:** Run the same command with **`--help`** immediately after the full `cex …` subcommand path (before any other flags), e.g. `gate-cli cex spot account get --help`, to see whether the CLI marks any flags or arguments as **required**.
2. **If `--help` lists required fields** (e.g. `--currency`): obtain values (ask the user only for non-secret business inputs such as symbol or amount; never ask for API secrets in chat), then run the **real** invocation **without** `--help`, including every required flag, e.g. `gate-cli cex spot account get --currency BTC`.
3. **If `--help` shows no required fields** for that subcommand: you may run the bare **`gate-cli cex …`** (only add optional flags the task still needs for correct semantics).

**Example:** To run `gate-cli cex spot account get` — first run `gate-cli cex spot account get --help`. If help indicates `--currency` is mandatory, supply it (e.g. `--currency BTC`), then execute `gate-cli cex spot account get --currency BTC`. If nothing is required beyond auth, execute `gate-cli cex spot account get` as documented.

If `--help` is ambiguous, prefer a safe read-only probe or explicit user clarification—especially before writes.

## 3. Authentication

- Public market-data endpoints may work without private account auth.
- If runtime policy requires API key, request valid key before analysis.

## 4. Optional resources

No mandatory auxiliary resources.

## 5. `gate-cli` command specification

Primary read-only tool families used by scenarios:
- Spot market data: `gate-cli cex spot market orderbook`, `gate-cli cex spot market tickers`, `gate-cli cex spot market candlesticks`
- Futures market data: `gate-cli cex futures market contract`, `gate-cli cex futures market orderbook`, `gate-cli cex futures market tickers`, `gate-cli cex futures market candlesticks`, `gate-cli cex futures market funding-rate`

Parameter rules:
- Always require explicit `currency_pair` / `contract` target.
- Case 8 slippage simulation requires both symbol and quote amount; do not auto-default.
- Candlestick calls must include timeframe aligned with the chosen scenario.

Common errors:
- Symbol not found / invalid market type: ask user to confirm symbol.
- Empty order book or stale feed: return insufficient-liquidity/data warning.

## 6. Execution SOP (Non-Skippable)

1. Route user intent to one scenario case (1-13).
2. Validate market type (spot or futures) and target symbol.
3. Collect required scenario inputs (especially Case 8 amount gate).
4. Execute tool sequence exactly in scenario order.
5. Apply scenario thresholds/rules to produce GO/CAUTION/BLOCK-like assessment language.
6. Return structured report with explicit data confidence.

## 7. Output Templates

```markdown
## Market Analysis Summary
- Scenario: {case_id_and_name}
- Target: {symbol_and_market}
- Key Signals: {liquidity_momentum_basis_liquidation_etc}
- Risk Flags: {high_slippage_thin_depth_event_risk}
- Conclusion: {bullish_bearish_neutral_with_conditions}
- Disclaimer: Data-driven analysis only, not investment advice.
```

## 8. Safety and Degradation Rules

1. Keep this skill strictly read-only.
2. Do not output fabricated prices, volumes, depth, or funding values.
3. When data is missing, degrade to partial analysis and label it clearly.
4. Do not infer execution recommendations as guaranteed outcomes.
5. Keep scenario-specific required inputs as hard gates (no hidden defaults for Case 8 amount/symbol).

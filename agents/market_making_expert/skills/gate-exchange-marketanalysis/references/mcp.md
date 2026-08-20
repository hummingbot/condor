---
name: gate-exchange-marketanalysis-mcp
version: "2026.3.30-1"
updated: "2026-03-30"
description: "MCP execution specification for market analysis scenarios including liquidity, momentum, liquidation, basis, and slippage simulation."
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

## 2. MCP Detection and Fallback

Detection:
1. Verify read-only market data tool families are available.
2. Probe with the smallest required endpoint for selected market type.

Fallback:
- If futures data endpoints are unavailable, provide spot-only analysis and disclose limitation.
- If both spot/futures endpoints are unavailable, return framework-level reasoning only and mark as data-unavailable.

## 3. Authentication

- Public market-data endpoints may work without private account auth.
- If runtime policy requires API key, request valid key before analysis.

## 4. MCP Resources

No mandatory MCP resources.

## 5. Tool Calling Specification

Primary read-only tool families used by scenarios:
- Spot market data: `cex_spot_get_spot_order_book`, `cex_spot_get_spot_tickers`, `cex_spot_get_spot_candlesticks`
- Futures market data: `cex_fx_get_fx_contract`, `cex_fx_get_fx_order_book`, `cex_fx_get_fx_tickers`, `cex_fx_get_fx_candlesticks`, `cex_fx_get_fx_funding_rate`

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

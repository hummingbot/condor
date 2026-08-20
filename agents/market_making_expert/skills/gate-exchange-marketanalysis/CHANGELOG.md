# Changelog

All notable changes to the gate-exchange-marketanalysis skill are documented here.

Format: date-based versioning (`YYYY.M.DD`). Same-day releases may use a sequential suffix: `YYYY.M.DD-1`, `YYYY.M.DD-2`, etc.

---

## [2026.3.11-1] - 2026-03-11

### Changed

- **`gate-cli` command names** — Aligned with new gate-cli interface naming:
  - Spot: `get_spot_*` → `cex_spot_get_spot_*` (e.g. `get_spot_order_book` → `gate-cli cex spot market orderbook`, `get_spot_tickers` → `gate-cli cex spot market tickers`, `get_spot_candlesticks` → `gate-cli cex spot market candlesticks`, `get_spot_trades` → `gate-cli cex spot market trades`).
  - Futures: `get_futures_*` / `list_futures_*` → `cex_fx_*` (e.g. `get_futures_contract` → `gate-cli cex futures market contract`, `get_futures_order_book` → `gate-cli cex futures market orderbook`, `get_futures_tickers` → `gate-cli cex futures market tickers`, `get_futures_candlesticks` → `gate-cli cex futures market candlesticks`, `get_futures_trades` → `gate-cli cex futures market trades`, `get_futures_funding_rate` → `gate-cli cex futures market funding-rate`, `list_futures_liq_orders` → `gate-cli cex futures market liquidations`, `get_futures_premium_index` → `gate-cli cex futures market premium`).
  - Updated in `SKILL.md`, `references/scenarios.md`, `README.md`, and `CHANGELOG.md`.

---

## [2026.3.7-1] - 2026-03-07

### Scope

- Case 8 adds **slippage simulation**: market-order fill vs order book, slippage reported as deviation from best ask (points and %). Spot and futures supported. **Requires both currency pair and quote amount** from the user; no defaults — when either is missing, prompt the user instead of running the simulation.
- Case 9 adds **K-line breakout / support–resistance** analysis from candlesticks and tickers.
- Case 10 adds **liquidity + weekend vs weekday** comparison using order book, 90d candlesticks, and tickers.

### Added

- **Case 8: Slippage simulation** — market-order slippage vs best ask
  - Trigger: e.g. "slippage simulation", "market buy $10K, how much slippage?", "ADA_USDT slippage simulation"
  - **Spot:** `gate-cli cex spot market orderbook` → `gate-cli cex spot market tickers`; **Futures:** `gate-cli cex futures market contract` → `gate-cli cex futures market orderbook` → `gate-cli cex futures market tickers`
  - Logic: walk ask ladder for quote amount Q; volume-weighted avg price; slippage = avg price − ask1 (points and %)
  - Output: simulation inputs, fill summary, slippage vs best ask, conclusion
- Scenario 8.1: spot slippage simulation (e.g. ADA_USDT / ETH market buy $10K)
- Scenario 8.2: futures slippage simulation (perpetual/contract market long)
- Scenario 8.3: missing pair or amount — prompt user (do not call `gate-cli`; ask for pair and/or quote amount; do not default to $10K)
- **Case 9: K-line breakout / support–resistance** — analyze breakout and support/resistance from recent K-line
  - Trigger: e.g. "Based on recent K-line, does SOL/USDT show signs of breaking out upward? Analyze support and resistance."
  - **Spot:** `gate-cli cex spot market candlesticks` → `gate-cli cex spot market tickers`; **Futures:** `gate-cli cex futures market candlesticks` → `gate-cli cex futures market tickers`
  - Logic: historical candlesticks for support/resistance; 24h price, volume, change for momentum and breakout signs
  - Output: K-line context, support & resistance table, momentum (24h), breakout assessment
- **Case 10: Liquidity + weekend vs weekday** — evaluate liquidity and compare weekend vs weekday
  - Trigger: e.g. "Evaluate ETH liquidity on the exchange and compare weekend vs weekday."
  - **Spot:** `gate-cli cex spot market orderbook` → `gate-cli cex spot market candlesticks`(90d) → `gate-cli cex spot market tickers`; **Futures:** `gate-cli cex futures market contract` → `gate-cli cex futures market orderbook` → `gate-cli cex futures market candlesticks`(90d) → `gate-cli cex futures market tickers`
  - Logic: order book for current depth; 90d candlesticks split by weekend vs weekday for volume and return; compare and summarize
  - Output: current liquidity, 90-day weekend vs weekday table, comparison, conclusion

### Changed

- **`gate-cli` command names** — Corrected to match gate-cli: `list_futures_funding_rate` → `get_futures_funding_rate`; `list_futures_premium_index` → `get_futures_premium_index`. Updated in `references/scenarios.md` and README.md. (`list_futures_liq_orders` is correct per `gate-cli`.)
- **Pure English** — all trigger phrases and report templates in SKILL.md and `references/scenarios.md` are in English.
- **Versioning** — version and `updated` follow current date (`YYYY.M.DD`).
- **Case 8 required inputs** — currency pair and quote amount both required; if either missing, prompt user (no default pair, no default amount e.g. $10K). SKILL.md Execution step 3 and Domain Knowledge (Case 8) updated accordingly.

### Audit

- Case 8 uses gate-cli only (`gate-cli cex spot market orderbook` / `gate-cli cex futures market orderbook`, `gate-cli cex spot market tickers` / `gate-cli cex futures market tickers`).
- No `gate-cli` calls when pair or amount is missing; user is prompted first.
- All analysis is read-only; no trading operations.

---

## [2026.3.5-1] - 2026-03-05

### Scope

This skill supports **market tape analysis only** (read-only): liquidity, momentum, liquidation, funding arbitrage, basis, manipulation risk, order book explainer. No trading operations.

### Added
- Initial release (market tape analysis, seven scenarios)
- Routing-based SKILL.md with document loading from `references/scenarios.md`
- **Seven analysis modules:** Liquidity, Momentum, Liquidation monitoring, Funding arbitrage, Basis, Manipulation risk, Order book explainer
- Smart spot/futures market detection (perpetual/contract keywords)
- `gate-cli` call order and Report Template defined in `references/scenarios.md`
- Domain knowledge and safety rules

### Audit

- Uses gate-cli tools only; all analysis is read-only
- No trading operations or credential handling in this skill


# Learnings

## Market Observations

## Execution Notes
- [2026-07-13 22:13] [trend_position] position_executor API requires connector_name, trading_pair, side, amount (base) — not the chain_network/base_token/amount_quote fields in the scan entry_template. The two schemas don't align; need correct connector_name (e.g. jupiter_solana-mainnet-beta?) and base-amount conversion before any entry can be created.

## Retired Insights

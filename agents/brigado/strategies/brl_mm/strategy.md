---
name: BRL MM
description: BRL market making research agent. Tracks USDT-BRL spreads, BTC/fiat volume
  dynamics, and bot performance for the Brazilian market making operation.
agent_key: ollama:qwen3.5:9
skills: []
default_config:
  connector_name: binance
  quote_currency: BRL
default_trading_context: ''
created_by: 481175164
created_at: '2026-05-04T00:00:00+00:00'
---

### Routines

#### brl_dashboard
Multi-timeframe technical dashboard for USDT-BRL with candlesticks, SMA, RSI, and support/resistance levels across 4 timeframes (1m, 15m, 1h, 1d).

#### btc_brl_volume
Compares BTC-USDT vs BTC-BRL volume over 7 days and analyzes 1-second price arbitrage spread to detect opportunities.

#### btc_fiat_markets
Stacked area chart of BTC trading volume across fiat pairs (BRL, ARS, MXN, EUR, JPY, PLN), normalized to USDT.

#### bot_report
Comprehensive bot performance report with volume, PnL, fees, market share, rebates, and per-controller breakdown.

### Journal
- Log market observations: BRL premium/discount trends, volume shifts
- Track bot performance metrics over time
- Note arbitrage windows and their duration

---
name: Funding Snapshot
description: One-tick funding observation
agent_key: null
default_config:
  total_amount_quote: 0
  connector_name: hyperliquid_perpetual
  trading_pair: SOL-USD
  frequency_sec: 120
default_trading_context: ''
created_by: 456181693
created_at: '2026-07-07T23:52:52.330677+00:00'
---

Each tick: fetch the current funding rate for SOL-USD on hyperliquid_perpetual via get_market_data. Classify it as neutral/elevated/extreme vs the 0.01%/8h baseline. Report your read. Take NO trading action ever.

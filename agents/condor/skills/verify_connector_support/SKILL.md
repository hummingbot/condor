---
name: verify_connector_support
description: Check what a connector actually supports before answering capability
  questions
when_to_use: User asks "can I use connector X?" or "does Y support Z?" — any capability
  question about a connector or DEX
created: '2026-08-12T11:51:59Z'
source: chat
---

## Verify Connector Support

Never guess connector capabilities from memory. Always pull the authoritative source first.

### Steps

1. **LP / CLMM questions** → `manage_executors(executor_type="lp_executor")` — read the "Supported DEXs" section
2. **AMM swap/pool-creation questions** → `manage_amm()` (no action) — read the connector list
3. **Pool discovery questions** → `explore_dex_pools` tool description lists supported connectors
4. **Market data / candle questions** → `get_market_data` guide or the `candles_without_a_candle_feed` skill

Then answer from what the guide actually says — not from what you remember.

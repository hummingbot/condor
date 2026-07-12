# Consult funding_rate_watcher_1

## Task

What is the current funding rate on SOL-USD? One short paragraph.

## Session

💭 **Reasoning**

> The user is consulting me as a funding-rate specialist for Hyperliquid perpetuals, asking about the current funding rate on SOL-USD. I need to fetch current market data using the get_market_data tool. Let me load the tool schema first.

💬 I'll fetch the current SOL-USD market data from Hyperliquid now.

🔧 **1. ToolSearch** (completed)

💭 **Reasoning**

> Now I can fetch the funding rate for SOL-USD on Hyperliquid perpetual.

🔧 **2. mcp__mcp-hummingbot__get_market_data** (completed)

💬 SOL-USD funding on Hyperliquid is currently **0.0013% per 8h** — well below the ~0.01%/8h baseline, making it essentially neutral with a slight long-paying bias. At this rate, carry cost for longs is negligible (~0.058% annualized), and there is no meaningful signal of crowded positioning in either direction. The mark/index spread of -$0.01 confirms the contract is trading nearly at fair value. This is not a funding-driven setup worth trading: neither a short-funding carry trade (rates too low to harvest) nor a crowding concern (rates not elevated enough to flag elevated liquidation risk on longs).

## Answer

I'll fetch the current SOL-USD market data from Hyperliquid now.SOL-USD funding on Hyperliquid is currently **0.0013% per 8h** — well below the ~0.01%/8h baseline, making it essentially neutral with a slight long-paying bias. At this rate, carry cost for longs is negligible (~0.058% annualized), and there is no meaningful signal of crowded positioning in either direction. The mark/index spread of -$0.01 confirms the contract is trading nearly at fair value. This is not a funding-driven setup worth trading: neither a short-funding carry trade (rates too low to harvest) nor a crowding concern (rates not elevated enough to flag elevated liquidation risk on longs).

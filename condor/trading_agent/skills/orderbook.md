# Orderbook Analysis

## Description
Analyzes bid/ask depth, spread, and imbalance for the trading pair.

## Prompt
Analyze the order book for {trading_pair} on {connector_name}:
1. Call get_market_data(action="orderbook", connector_name="{connector_name}", trading_pair="{trading_pair}")
2. Compute spread percentage: (best_ask - best_bid) / best_bid * 100
3. Assess depth imbalance in top 10 levels (bid_volume vs ask_volume)
4. Report: current mid price, spread %, bid/ask imbalance ratio, and whether conditions favor buying or selling

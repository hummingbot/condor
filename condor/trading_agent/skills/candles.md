# Candles

## Description
Analyzes recent OHLCV candles for trend and volatility detection.

## Prompt
Analyze recent candles for {trading_pair} on {connector_name}:
1. Call get_market_data(action="candles", connector_name="{connector_name}", trading_pair="{trading_pair}", interval="{candle_interval}")
2. Look at the last 50 bars
3. Determine trend: compare average of last 5 closes vs previous 5
4. Calculate volatility: (high - low) / current_price
5. Identify support/resistance levels from swing highs/lows
6. Report: trend direction, volatility %, key levels

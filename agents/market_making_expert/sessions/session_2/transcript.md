# Consult market_making_expert_2

## Task

In one short paragraph: what regime checks do you run before deploying a PMM Mister bot? Do not call any tools.

## Session

💭 **Reasoning**

> The user is asking the market making expert agent a question about regime checks before deploying a PMM Mister bot. This is a consulting question that doesn't require any tool calls - just domain knowledge about the regime detection process.

💬 Before deploying, I run four checks in sequence. First, **trend check**: pull recent candles (15m and 1h) and compute ADX — if ADX > 25 with consistent candle bodies in one direction, the market is trending and symmetric quoting will bleed inventory toward the trend. Second, **volatility check**: compare ATR now vs. the prior 24h average — if ATR is expanding sharply or funding rate has spiked, the market is in a volatile regime where wide spreads or a pause is safer than deployment. Third, **mean-reversion check**: if ADX < 20 and price is oscillating around a moving average with narrow Bollinger bandwidth, that's a ranging regime — the most favorable for PMM Mister, so spreads can be tighter. Fourth, **inventory baseline**: check current portfolio balances to see if the account already has a skewed position in the base asset before the bot starts, since deploying into an already-skewed book amplifies one-sided risk. Only after confirming we're in a ranging or quiet regime (or have calibrated spreads for the detected regime) do I proceed to config and deployment.

## Answer

Before deploying, I run four checks in sequence. First, **trend check**: pull recent candles (15m and 1h) and compute ADX — if ADX > 25 with consistent candle bodies in one direction, the market is trending and symmetric quoting will bleed inventory toward the trend. Second, **volatility check**: compare ATR now vs. the prior 24h average — if ATR is expanding sharply or funding rate has spiked, the market is in a volatile regime where wide spreads or a pause is safer than deployment. Third, **mean-reversion check**: if ADX < 20 and price is oscillating around a moving average with narrow Bollinger bandwidth, that's a ranging regime — the most favorable for PMM Mister, so spreads can be tighter. Fourth, **inventory baseline**: check current portfolio balances to see if the account already has a skewed position in the base asset before the bot starts, since deploying into an already-skewed book amplifies one-sided risk. Only after confirming we're in a ranging or quiet regime (or have calibrated spreads for the detected regime) do I proceed to config and deployment.

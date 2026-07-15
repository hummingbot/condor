# Async Patterns

Parallelism and rate-limiting for routines that fetch data for multiple pairs,
markets, or resources.

## Sequential (fine for ≤3 independent calls)

```python
candles = await client.market_data.get_candles(connector, pair, "1h", max_records=100)
prices = await client.market_data.get_prices(connector, pairs)
```

## Parallel: asyncio.gather (4+ independent calls)

```python
import asyncio

candles, prices, funding = await asyncio.gather(
    client.market_data.get_candles(connector, pair, "1h", max_records=100),
    client.market_data.get_prices(connector, pairs),
    client.market_data.get_funding_info(connector, pair),
)
```

## Bulk parallel with rate limiting (10+ calls)

```python
import asyncio

sem = asyncio.Semaphore(10)  # max 10 concurrent requests

async def fetch_one(pair: str) -> dict:
    async with sem:
        try:
            return await client.market_data.get_candles(connector, pair, "1h", max_records=100)
        except Exception as e:
            logger.warning(f"Failed {pair}: {e}")
            return {}

results = await asyncio.gather(*[fetch_one(p) for p in pairs])
# results[i] corresponds to pairs[i]; failed ones return {}
```

## Safe single-call wrapper

```python
async def safe_fetch(coro):
    try:
        return await coro
    except Exception as e:
        logger.warning(f"Fetch failed: {e}")
        return None

prices = await safe_fetch(client.market_data.get_prices(connector, pairs))
if prices is None:
    return "Failed to fetch market data"
```

## Rules

- Use `asyncio.gather` for 4+ independent calls — never loop with sequential awaits
- Always use `Semaphore(10)` for bulk calls (10+ items) to avoid rate limits
- Catch exceptions **inside** gather tasks, not outside — one failure should not kill the batch
- Never use `time.sleep` — always `asyncio.sleep`
- Filter out empty/None results after gathering before processing

/**
 * How much history one GeckoTerminal candle request can carry.
 *
 * Pool charts (every Gateway DEX network, plus `xrpl`) are drawn from
 * GeckoTerminal's OHLCV endpoint, which answers with a *count* of candles, never
 * a window — and caps that count at 1000 per request (asking for more is a 400,
 * not a truncation). So the interval a chart picks decides how far back it can
 * reach at all: at 1m a request covers ~16h, at 15m ~10 days.
 *
 * That cap is also the whole rate-limit story. GeckoTerminal's budget is counted
 * in *requests*, not candles, and it is shared by every chart, pool list and
 * price on the dashboard (see `_GECKO_RATE_LIMIT` in `condor/pool_data.py`), so a
 * window that needs two requests to draw costs twice as much as the same window
 * at an interval that fits in one. Charts should offer, by default, the window a
 * single request already pays for.
 *
 * Mirrors `GECKO_TIMEFRAMES` / `GECKO_OHLCV_MAX` in `condor/pool_data.py` — the
 * backend snaps any other interval down to one of these before calling upstream.
 */

/** Candles GeckoTerminal returns for one OHLCV request, at most. */
export const GECKO_MAX_CANDLES = 1000;

/** The only aggregates GeckoTerminal's OHLCV endpoint accepts, coarsest last. */
export const GECKO_TIMEFRAMES: { interval: string; seconds: number }[] = [
  { interval: "1m", seconds: 60 },
  { interval: "5m", seconds: 300 },
  { interval: "15m", seconds: 900 },
  { interval: "1h", seconds: 3600 },
  { interval: "4h", seconds: 14400 },
  { interval: "12h", seconds: 43200 },
  { interval: "1d", seconds: 86400 },
];

/** Seconds one candle of `interval` covers; 60 for anything unrecognized. */
export function geckoIntervalSeconds(interval: string): number {
  return GECKO_TIMEFRAMES.find((t) => t.interval === interval)?.seconds ?? 60;
}

/** How far back one request of `interval` candles reaches, in seconds. */
export function geckoIntervalSpan(interval: string): number {
  return geckoIntervalSeconds(interval) * GECKO_MAX_CANDLES;
}

/**
 * The finest GeckoTerminal interval whose 1000 candles still cover `seconds`.
 *
 * The chart asks for a window; picking the interval from that window is what
 * keeps the answer both complete and one request wide. Without it a long LP
 * position charted at 1m silently draws only its last ~16h — the cap trims the
 * *oldest* candles, which are exactly the ones holding the entry.
 *
 * Falls back to the coarsest timeframe for a window even 1d candles cannot span
 * (~2.7 years); that chart is clipped either way, and clipped at 1d loses the
 * least.
 */
export function geckoIntervalForSpan(seconds: number): string {
  const fits = GECKO_TIMEFRAMES.find((t) => t.seconds * GECKO_MAX_CANDLES >= seconds);
  return (fits ?? GECKO_TIMEFRAMES[GECKO_TIMEFRAMES.length - 1]).interval;
}

import { QueryClient } from "@tanstack/react-query";

/**
 * App-wide TanStack Query cache.
 *
 * Module-scope singleton (rather than created inside `App`) so that non-React
 * code can reach it — notably the logout path in `lib/auth.ts`, which must drop
 * every cached response before the next user takes over the tab.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
    },
  },
});

/**
 * Granularity (seconds) the candle window bounds are snapped to before they
 * enter a query key. Callers pad their windows by far more than this, so the
 * rounding never changes which candles are usable — it only stops a
 * `Date.now()`-derived bound from minting a new key (and a new request) on
 * every render.
 */
const CANDLE_WINDOW_BUCKET = 300;

/**
 * Cache key + fetch window for a candles request.
 *
 * The window is part of the key on purpose: the same market can be charted over
 * completely different time ranges (two agent sessions, a prefetched default
 * range), and those must not share a cache entry — otherwise the second chart
 * silently renders the first one's candles. Bounds are snapped outward, so the
 * fetched window always covers the requested one.
 *
 * Fetch with the returned `startTime`/`endTime` rather than the raw bounds, so
 * the key always describes the data it holds.
 */
export function candlesQuery(
  server: string,
  connector: string,
  pair: string,
  interval: string,
  start?: number,
  end?: number,
) {
  const startTime =
    start === undefined ? undefined : Math.floor(start / CANDLE_WINDOW_BUCKET) * CANDLE_WINDOW_BUCKET;
  const endTime =
    end === undefined ? undefined : Math.ceil(end / CANDLE_WINDOW_BUCKET) * CANDLE_WINDOW_BUCKET;
  return {
    startTime,
    endTime,
    queryKey: ["candles", server, connector, pair, interval, startTime ?? null, endTime ?? null],
  };
}

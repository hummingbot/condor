import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import type { CandleData } from "@/lib/api";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import { intervalSeconds } from "@/lib/gridExecutor";

/**
 * How long a candle channel may go without a frame before it reads as stalled.
 *
 * The backend sends a frame only when the newest candle *changed*
 * (`condor/web/streams/candles.py`, the "Broadcast if we have newer candles OR
 * ... OHLCV changed" branch), so silence is normal on a quiet market, and the
 * cadence a healthy feed can legitimately fall to has two parts:
 *
 * - the poll itself: a CLOB channel re-fetches every `min(interval, 10)` s, a
 *   DEX pool every `max(30, min(interval / 5, 180))` s (`_gecko_poll_interval`,
 *   `_GECKO_POLL_MIN`/`_GECKO_POLL_MAX`). Two missed polls at the 180 s ceiling
 *   is the floor here — one slow or rate-limited fetch is not a dead feed;
 * - the candle: with no trades the forming bar does not move, and the only
 *   change a poll is then sure to see is the next candle opening. Two intervals
 *   covers one whole silent candle plus the poll that finds its successor.
 *
 * The old 30 s (sub-1h) / 120 s thresholds sat *below* the DEX poll period, so
 * every healthy pool chart was flagged between two polls.
 *
 * The 180 s ceiling belongs to the GeckoTerminal-polled channels only. Applied
 * to every channel it made a dead exchange feed on a 1m chart take six minutes
 * to say so; an exchange channel is floored by its own 10 s poll instead, which
 * leaves the two-candle term to decide (two minutes on a 1m chart).
 */
const GECKO_SLOWEST_POLL_MS = 180_000; // _GECKO_POLL_MAX
const CLOB_SLOWEST_POLL_MS = 10_000; // min(interval, 10) s
const STALE_CHECK_INTERVAL_MS = 10_000; // check every 10s

/** `geckoPolled`: the channel is fed by the GeckoTerminal poll, not an exchange. */
export function staleThresholdMs(interval: string, geckoPolled = false): number {
  const slowestPoll = geckoPolled ? GECKO_SLOWEST_POLL_MS : CLOB_SLOWEST_POLL_MS;
  return Math.max(2 * slowestPoll, 2 * intervalSeconds(interval) * 1000);
}

/**
 * React hook bridging the singleton candle store to components.
 *
 * On mount: subscribes to the candle channel and registers an update listener.
 * On unmount: unsubscribes (old data stays in store for 5 min).
 * On key change: unsubscribes old, subscribes new.
 */
export function useCandleStore(
  server: string | null,
  connector: string,
  pair: string,
  interval: string,
  /**
   * Pin the stream to one DEX pool. A gateway pair like `SOL-USDC` exists in
   * dozens of pools, and without this the backend charts whichever one it judges
   * deepest — which is exactly the guess the DEX page exists to overturn. Omitted
   * (every CLOB chart) the channel keeps its five segments, byte for byte.
   */
  poolAddress?: string,
  /**
   * The venue's candles come from the GeckoTerminal poll (`gecko_candles` on
   * `GET /market/venues`), which sets how long a silence is still healthy. A
   * pinned pool is always one, so `poolAddress` alone implies it.
   */
  geckoCandles = false,
): {
  candles: CandleData[];
  isStale: boolean;
  mergeCandles: (c: CandleData[]) => void;
  setDuration: (seconds: number) => void;
} {
  const key = server ? candleChannelKey(server, connector, pair, interval, poolAddress) : "";

  const geckoPolled = geckoCandles || !!poolAddress;

  const [candles, setCandles] = useState<CandleData[]>([]);
  const [isStale, setIsStale] = useState(false);
  const keyRef = useRef(key);

  useEffect(() => {
    if (!key) {
      setCandles([]);
      setIsStale(false);
      return;
    }

    keyRef.current = key;

    // Subscribe — returns cached candles instantly, or [] for a cold channel.
    // Written unconditionally: retaining the previous key's candles here would
    // leak one market's prices into another for callers that are not remounted
    // on a pair change (CreateExecutor's `currentPrice`, for one).
    const cached = candleStore.subscribe(key);
    setCandles(cached);
    setIsStale(false); // freshly subscribed; the periodic check re-evaluates below

    // Listen for updates
    const removeListener = candleStore.onUpdate(key, (updated) => {
      if (keyRef.current === key) {
        setCandles(updated);
        setIsStale(false); // got fresh data
      }
    });

    // Periodic staleness check
    const threshold = staleThresholdMs(interval, geckoPolled);
    const timer = setInterval(() => {
      if (keyRef.current !== key) return;
      const age = candleStore.getLastUpdateAge(key);
      setIsStale(age > threshold);
    }, STALE_CHECK_INTERVAL_MS);

    return () => {
      removeListener();
      clearInterval(timer);
      candleStore.unsubscribe(key);
    };
  }, [key, interval, geckoPolled]);

  const mergeCandles = (c: CandleData[]) => {
    if (key) candleStore.mergeCandles(key, c);
  };

  const setDuration = (seconds: number) => {
    // With the interval, so the store keeps as many candles as the range holds.
    if (key) candleStore.setDuration(key, seconds, intervalSeconds(interval));
  };

  return { candles, isStale, mergeCandles, setDuration };
}

/** The close of the newest candle, or null for an empty channel. */
function lastCloseOf(candles: CandleData[]): number | null {
  return candles[candles.length - 1]?.close ?? null;
}

/**
 * The last close of a candle channel, and nothing else.
 *
 * `useCandleStore` hands back a fresh array on every frame, so a caller that
 * wants one scalar re-renders its whole tree at candle-tick rate — once a
 * second — for a number that usually hasn't moved. Reading the channel as an
 * external store whose snapshot *is* the close leaves React to compare two
 * numbers: an unchanged close costs a comparison instead of a render pass, and
 * the snapshot is recomputed for the new channel the moment the key changes, so
 * no market's price can outlive its pair.
 *
 * A null `server` means "no subscription", exactly as in `useCandleStore` —
 * that is how a caller gates itself off the stream entirely (a venue whose
 * price comes from REST has no reason to be on it).
 */
export function useLastClose(
  server: string | null,
  connector: string,
  pair: string,
  interval: string,
  poolAddress?: string,
): number | null {
  const key = server ? candleChannelKey(server, connector, pair, interval, poolAddress) : "";

  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (!key) return () => {};
      candleStore.subscribe(key);
      const removeListener = candleStore.onUpdate(key, onStoreChange);
      return () => {
        removeListener();
        candleStore.unsubscribe(key);
      };
    },
    [key],
  );

  const getSnapshot = useCallback(
    () => (key ? lastCloseOf(candleStore.getCandles(key)) : null),
    [key],
  );

  return useSyncExternalStore(subscribe, getSnapshot);
}

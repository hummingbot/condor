import { useEffect, useRef, useState } from "react";

import type { CandleData } from "@/lib/api";
import { candleChannelKey, candleStore } from "@/lib/candle-store";

/** Staleness thresholds by interval category */
const STALE_THRESHOLD_SUB_1H_MS = 30_000; // 30s for intervals < 1h
const STALE_THRESHOLD_1H_PLUS_MS = 120_000; // 2min for intervals >= 1h
const STALE_CHECK_INTERVAL_MS = 10_000; // check every 10s

function getStaleThreshold(interval: string): number {
  const hourPlus = ["1h", "2h", "4h", "1d", "1w"];
  return hourPlus.includes(interval) ? STALE_THRESHOLD_1H_PLUS_MS : STALE_THRESHOLD_SUB_1H_MS;
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
): {
  candles: CandleData[];
  isStale: boolean;
  mergeCandles: (c: CandleData[]) => void;
  setDuration: (seconds: number) => void;
} {
  const key = server ? candleChannelKey(server, connector, pair, interval, poolAddress) : "";

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
    const threshold = getStaleThreshold(interval);
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
  }, [key, interval]);

  const mergeCandles = (c: CandleData[]) => {
    if (key) candleStore.mergeCandles(key, c);
  };

  const setDuration = (seconds: number) => {
    if (key) candleStore.setDuration(key, seconds);
  };

  return { candles, isStale, mergeCandles, setDuration };
}

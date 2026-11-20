/**
 * A channel keeps as many candles as the range asked of it holds (FP-02).
 *
 * Every channel was capped at 2,000 candles whatever the chart's range, so a
 * 3-day range of 1m candles (4,320) kept its last 33.3 hours and evicted the
 * rest on every merge. `setDuration` now sizes the channel from the range —
 * up to a hard ceiling, and never back down.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleData } from "./api";
import { COLLECTION_SIZE_CEILING, candleStore } from "./candle-store";
import { MAX_CHART_CANDLES } from "./gridExecutor";

const NEWEST = 1_700_000_000;

/** `count` 1m candles ending at NEWEST, oldest first. */
function minutes(count: number): CandleData[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: NEWEST - (count - 1 - i) * 60,
    open: 1,
    high: 2,
    low: 0.5,
    close: 1.5,
    volume: 1,
  }));
}

let n = 0;
let key = "";

beforeEach(() => {
  // A fresh channel per case: the store is a singleton.
  key = `candles:test:binance:BTC-USDT:1m:${n++}`;
});

afterEach(() => {
  candleStore.collections.delete(key);
  vi.restoreAllMocks();
});

describe("candleStore channel size", () => {
  it("keeps the default 2,000 when no range was ever asked for", () => {
    candleStore.mergeCandles(key, minutes(4320));
    expect(candleStore.getCandles(key)).toHaveLength(2000);
  });

  it("keeps a whole 3-day range of 1m candles once the chart asks for it", () => {
    candleStore.setDuration(key, 3 * 86400, 60);
    candleStore.mergeCandles(key, minutes(4320));

    const kept = candleStore.getCandles(key);
    expect(kept).toHaveLength(4320);
    expect(kept[0].timestamp).toBe(NEWEST - 4319 * 60);
  });

  it("never shrinks: a shorter range asked later keeps the longer one's candles", () => {
    candleStore.setDuration(key, 3 * 86400, 60);
    candleStore.setDuration(key, 3600, 60);
    candleStore.mergeCandles(key, minutes(4320));
    expect(candleStore.getCandles(key)).toHaveLength(4320);
  });

  it("stops at the hard ceiling however long the range", () => {
    candleStore.setDuration(key, 30 * 86400, 60);
    candleStore.mergeCandles(key, minutes(20_000));
    expect(candleStore.getCandles(key)).toHaveLength(COLLECTION_SIZE_CEILING);
  });

  it("has a ceiling that holds a chart's whole candle budget", () => {
    expect(COLLECTION_SIZE_CEILING).toBeGreaterThan(MAX_CHART_CANDLES);
  });
});

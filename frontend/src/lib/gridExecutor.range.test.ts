/**
 * A range implies a finest interval (FP-02).
 *
 * The interval and range pickers were independent while the candle store kept
 * 2,000 candles per channel, so 1m × 30d drew the last 33 hours under a "30d"
 * button — after downloading all 43,200 candles. The budget is now a week of
 * minutes, and a range that would need more steps the interval up instead.
 */

import { describe, expect, it } from "vitest";

import {
  INTERVALS,
  LOOKBACK_OPTIONS,
  MAX_CHART_CANDLES,
  intervalFloor,
  intervalSeconds,
  keptLookbackSeconds,
  resolveChartInterval,
} from "@/lib/gridExecutor";

const DAY = 86400;
const DEX = ["1m", "5m", "15m"];

describe("intervalFloor", () => {
  it("allows 1m up to a week and steps to 5m beyond it", () => {
    expect(intervalFloor(3600)).toBe("1m");
    expect(intervalFloor(7 * DAY)).toBe("1m");
    expect(intervalFloor(14 * DAY)).toBe("5m");
    expect(intervalFloor(30 * DAY)).toBe("5m");
  });

  it("keeps every offered range inside the budget at its floor", () => {
    for (const { seconds } of LOOKBACK_OPTIONS) {
      for (const intervals of [INTERVALS, DEX]) {
        const floor = intervalFloor(seconds, intervals);
        expect(seconds / intervalSeconds(floor)).toBeLessThanOrEqual(MAX_CHART_CANDLES);
      }
    }
  });

  it("answers the coarsest interval when even that cannot hold the range", () => {
    expect(intervalFloor(400 * DAY, DEX)).toBe("15m");
  });
});

describe("resolveChartInterval", () => {
  it("leaves a pick that fits alone", () => {
    expect(resolveChartInterval("1m", DAY)).toBe("1m");
    expect(resolveChartInterval("1h", 30 * DAY)).toBe("1h");
  });

  it("steps a too-fine pick up to the floor", () => {
    expect(resolveChartInterval("1m", 30 * DAY)).toBe("5m");
    expect(resolveChartInterval("1m", 30 * DAY, DEX)).toBe("5m");
  });
});

describe("keptLookbackSeconds", () => {
  it("is the whole range when it fits, and the budget's span when it does not", () => {
    expect(keptLookbackSeconds(3 * DAY, "1m")).toBe(3 * DAY);
    expect(keptLookbackSeconds(30 * DAY, "1m")).toBe(MAX_CHART_CANDLES * 60);
  });
});

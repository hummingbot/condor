import { describe, expect, it } from "vitest";

import {
  GECKO_MAX_CANDLES,
  geckoIntervalForSpan,
  geckoIntervalSpan,
} from "./gecko-candles";

const HOUR = 3600;
const DAY = 86400;

describe("geckoIntervalForSpan", () => {
  it("keeps 1m for a window one request of 1m candles covers", () => {
    // ~16h is all 1000 one-minute candles reach; a short position stays fine-grained.
    expect(geckoIntervalForSpan(6 * HOUR)).toBe("1m");
  });

  it("steps up rather than clipping the oldest candles", () => {
    // The failure this exists to prevent: a week-long LP position drawn at 1m
    // comes back trimmed to its last ~16h — losing the entry the chart is for.
    expect(geckoIntervalForSpan(7 * DAY)).toBe("15m");
  });

  it("picks the finest interval that still fits, never a coarser one", () => {
    for (const seconds of [HOUR, 20 * HOUR, 3 * DAY, 30 * DAY, 200 * DAY]) {
      const chosen = geckoIntervalForSpan(seconds);
      expect(geckoIntervalSpan(chosen)).toBeGreaterThanOrEqual(seconds);
    }
    // One step finer would not have fit 30 days.
    expect(geckoIntervalForSpan(30 * DAY)).toBe("1h");
    expect(geckoIntervalSpan("15m")).toBeLessThan(30 * DAY);
  });

  it("falls back to the coarsest timeframe past what any request spans", () => {
    expect(geckoIntervalForSpan(10 * 365 * DAY)).toBe("1d");
  });

  it("spans the cap, not a page", () => {
    expect(geckoIntervalSpan("15m")).toBe(900 * GECKO_MAX_CANDLES);
  });
});

describe("the pool page defaults", () => {
  it("offers a window one request already pays for", () => {
    // DexPool opens on 15m over 7 days: 672 candles, inside the 1000 cap, so the
    // first paint of a pool costs exactly one GeckoTerminal request.
    expect((7 * DAY) / 900).toBeLessThanOrEqual(GECKO_MAX_CANDLES);
    expect(geckoIntervalForSpan(7 * DAY)).toBe("15m");
  });
});

import { describe, expect, it } from "vitest";

import {
  formatAxisCurrency,
  formatAxisTime,
  formatCompactVolume,
  formatCurrencyVolume,
  formatDateTime,
  formatTime,
} from "./formatters";

// The bug this suite pins (READ-251): the chart Y axes used to carry their own
// inline `K`-only ladder, so a $2.4M cumulative volume rendered "$2400.0K" on
// the axis while the header strip and the tooltip a few pixels away — both on
// `formatCurrencyVolume` — rendered "$2.4M". `formatAxisCurrency` is now the one
// ladder, and the "agrees with the neighbouring surfaces" block below is the
// part that must not regress when a later item touches axis or tooltip render.

describe("formatAxisCurrency", () => {
  describe("below $1K — the only tier where the axis has its own rule", () => {
    it("gives a pnl axis 2 decimals under $10, where the axis often spans cents", () => {
      expect(formatAxisCurrency(0, "$", "pnl")).toBe("$0.00");
      expect(formatAxisCurrency(2.5, "$", "pnl")).toBe("$2.50");
      expect(formatAxisCurrency(-2.5, "$", "pnl")).toBe("$-2.50");
      expect(formatAxisCurrency(9.99, "$", "pnl")).toBe("$9.99");
    });

    it("drops to whole dollars from $10 up on a pnl axis", () => {
      expect(formatAxisCurrency(10, "$", "pnl")).toBe("$10");
      expect(formatAxisCurrency(123.45, "$", "pnl")).toBe("$123");
      expect(formatAxisCurrency(999.4, "$", "pnl")).toBe("$999");
    });

    it("never shows decimals on a volume axis — ticks are whole dollars", () => {
      expect(formatAxisCurrency(0, "$", "volume")).toBe("$0");
      expect(formatAxisCurrency(2.5, "$", "volume")).toBe("$3");
      expect(formatAxisCurrency(250, "$", "volume")).toBe("$250");
      expect(formatAxisCurrency(999.4, "$", "volume")).toBe("$999");
    });

    it("defaults to the volume rule when no kind is given", () => {
      expect(formatAxisCurrency(2.5)).toBe("$3");
    });
  });

  describe("$1K to $1M — the K tier", () => {
    it("switches to K at exactly $1,000", () => {
      expect(formatAxisCurrency(1_000, "$", "pnl")).toBe("$1.0K");
      expect(formatAxisCurrency(1_000, "$", "volume")).toBe("$1.0K");
      expect(formatAxisCurrency(999.9, "$", "volume")).toBe("$1000");
    });

    it("renders one decimal across the tier, for both kinds alike", () => {
      expect(formatAxisCurrency(2_450, "$", "pnl")).toBe("$2.5K");
      expect(formatAxisCurrency(2_450, "$", "volume")).toBe("$2.5K");
      expect(formatAxisCurrency(999_900, "$", "volume")).toBe("$999.9K");
    });
  });

  describe("above $1M — the tier the axis used to be missing", () => {
    it("renders M instead of a four-digit K", () => {
      expect(formatAxisCurrency(1_000_000, "$", "volume")).toBe("$1.0M");
      expect(formatAxisCurrency(2_400_000, "$", "volume")).toBe("$2.4M");
      expect(formatAxisCurrency(14_500_000, "$", "volume")).toBe("$14.5M");
      expect(formatAxisCurrency(2_400_000, "$", "pnl")).toBe("$2.4M");
    });

    it("carries on to a B tier past $1B, as formatCompactVolume does", () => {
      expect(formatAxisCurrency(1_000_000_000, "$", "volume")).toBe("$1.00B");
      expect(formatAxisCurrency(2_400_000_000, "$", "volume")).toBe("$2.40B");
    });

    it("keeps every label short enough for the 52px axis gutter", () => {
      // At fontSize 10 the gutter fits ~8 glyphs; the old ladder printed
      // "$14500.0K" (9) and got clipped.
      for (const v of [0, -9.99, 999.4, -2_450, 999_900, 14_500_000, -2_400_000_000]) {
        expect(formatAxisCurrency(v, "$", "pnl").length).toBeLessThanOrEqual(8);
        expect(formatAxisCurrency(v, "$", "volume").length).toBeLessThanOrEqual(8);
      }
    });
  });

  describe("negatives and zero", () => {
    it("carries the minus inside the number, as the other helpers do", () => {
      expect(formatAxisCurrency(-2_400_000, "$", "volume")).toBe("$-2.4M");
      expect(formatAxisCurrency(-2_450, "$", "volume")).toBe("$-2.5K");
      expect(formatAxisCurrency(-250, "$", "volume")).toBe("$-250");
      expect(formatAxisCurrency(-1_000_000_000, "$", "volume")).toBe("$-1.00B");
    });

    it("renders zero without a sign in either kind", () => {
      expect(formatAxisCurrency(0, "$", "pnl")).toBe("$0.00");
      expect(formatAxisCurrency(-0, "$", "volume")).toBe("$0");
    });
  });

  it("honours a non-$ currency symbol at every tier", () => {
    expect(formatAxisCurrency(2_400_000, "€", "volume")).toBe("€2.4M");
    expect(formatAxisCurrency(2_450, "€", "volume")).toBe("€2.5K");
    expect(formatAxisCurrency(250, "€", "volume")).toBe("€250");
  });

  describe("agrees with the neighbouring surfaces", () => {
    // Header strip and both chart tooltips run every number through
    // `formatCurrencyVolume`; the axis must not disagree with them.
    const values = [1_000_000, 2_400_000, 14_500_000, -2_400_000, 999_900, 1_000];

    it("matches formatCurrencyVolume from $1K up", () => {
      for (const v of values) {
        expect(formatAxisCurrency(v, "$", "volume")).toBe(formatCurrencyVolume(v, "$"));
        expect(formatAxisCurrency(v, "$", "pnl")).toBe(formatCurrencyVolume(v, "$"));
      }
    });

    it("matches formatCompactVolume at every tier including B", () => {
      for (const v of [...values, 2_400_000_000, 250]) {
        expect(formatAxisCurrency(v, "$", "volume")).toBe(formatCompactVolume(v, "$"));
      }
    });
  });
});


// ── READ-250: the X axis was hardcoded to HH:MM ──
//
// The sampling interval is derived from the bot's real runtime (PERF-238), so
// one chart's span runs from a couple of hours to well over a year. At `HH:MM`
// a five-day window prints `08:00 / 12:00 / 16:00` once per day with nothing to
// separate the days. The rule `formatAxisTime` encodes is not "always prepend
// the date" but "the shortest label whose parts can still differ across this
// span" — which is why the date is *dropped* under a day and the time is
// dropped past a week. The uniqueness block at the end is the rule itself,
// stated as a property rather than as a list of examples.

const HOUR = 3_600_000;
const DAY = 86_400_000;

/** Local-time fixtures, so the expected labels hold in any TZ the suite runs in. */
const mar14 = new Date(2026, 2, 14, 8, 30).getTime();
const nov15 = new Date(2025, 10, 15, 8, 30).getTime();

/** The `n` ticks recharts would space evenly across a span ending at `end`. */
function ticks(end: number, spanMs: number, n = 6): number[] {
  return Array.from({ length: n }, (_, i) => end - spanMs + (i * spanMs) / (n - 1));
}

describe("formatAxisTime", () => {
  describe("under a day — the date is the same on every tick, so it is left off", () => {
    it("prints a bare HH:MM", () => {
      expect(formatAxisTime(mar14, 6 * HOUR)).toBe("08:30");
      expect(formatAxisTime(mar14, 23 * HOUR)).toBe("08:30");
    });

    it("falls back to HH:MM for a degenerate span (one point, or none)", () => {
      expect(formatAxisTime(mar14, 0)).toBe("08:30");
      expect(formatAxisTime(mar14, -1)).toBe("08:30");
      expect(formatAxisTime(mar14, NaN)).toBe("08:30");
    });
  });

  describe("a day to a week — both halves move, so both are shown", () => {
    it("switches to Mon D HH:MM at exactly 24h, the first span that can repeat a time", () => {
      expect(formatAxisTime(mar14, DAY - 1)).toBe("08:30");
      expect(formatAxisTime(mar14, DAY)).toBe("Mar 14 08:30");
    });

    it("keeps the day on a multi-day window — the bug this item fixes", () => {
      expect(formatAxisTime(mar14, 5 * DAY)).toBe("Mar 14 08:30");
      expect(formatAxisTime(new Date(2026, 2, 17, 16, 0).getTime(), 5 * DAY)).toBe("Mar 17 16:00");
    });
  });

  describe("a week to a year — ticks are over a day apart, so the time is decoration", () => {
    it("drops the time from 7 days up", () => {
      expect(formatAxisTime(mar14, 7 * DAY - 1)).toBe("Mar 14 08:30");
      expect(formatAxisTime(mar14, 7 * DAY)).toBe("Mar 14");
    });

    it("stays on Mon D across a multi-month window", () => {
      expect(formatAxisTime(mar14, 90 * DAY)).toBe("Mar 14");
      expect(formatAxisTime(nov15, 180 * DAY)).toBe("Nov 15");
    });

    it("needs no year to stay unambiguous across a new year", () => {
      // A month/day pair cannot repeat inside a span shorter than 365 days,
      // which is exactly where the next tier starts.
      const spanned = ticks(new Date(2026, 1, 13, 8, 30).getTime(), 90 * DAY);
      const labels = spanned.map((t) => formatAxisTime(t, 90 * DAY));
      expect(labels[0]).toBe("Nov 15");
      expect(labels[labels.length - 1]).toBe("Feb 13");
      expect(new Set(labels).size).toBe(labels.length);
    });
  });

  describe("a year and beyond — the day is decoration and the year is what repeats", () => {
    it("switches to Mon 'YY at 365 days", () => {
      expect(formatAxisTime(mar14, 365 * DAY - 1)).toBe("Mar 14");
      expect(formatAxisTime(mar14, 365 * DAY)).toBe("Mar '26");
      expect(formatAxisTime(nov15, 400 * DAY)).toBe("Nov '25");
    });

    it("separates the two halves of a span that crosses a year boundary", () => {
      expect(formatAxisTime(nov15, 400 * DAY)).toBe("Nov '25");
      expect(formatAxisTime(new Date(2026, 1, 13, 8, 30).getTime(), 400 * DAY)).toBe("Feb '26");
    });

    it("pads a single-digit year, so 2009 is not '9", () => {
      expect(formatAxisTime(new Date(2009, 5, 2).getTime(), 400 * DAY)).toBe("Jun '09");
    });
  });

  describe("agrees with the neighbouring surfaces", () => {
    it("reuses formatTime under a day and formatDateTime up to a week", () => {
      // The tooltip that opens over a tick runs on formatDateTime
      // (PnlChartTooltips); a tick must not spell the same instant differently.
      expect(formatAxisTime(mar14, 6 * HOUR)).toBe(formatTime(mar14));
      expect(formatAxisTime(mar14, 2 * DAY)).toBe(formatDateTime(mar14));
    });
  });

  describe("the rule itself: no two ticks of a span share a label", () => {
    const spans = [2 * HOUR, 12 * HOUR, DAY, 5 * DAY, 7 * DAY, 90 * DAY, 365 * DAY, 800 * DAY];

    it("labels every tick of every span distinctly", () => {
      for (const span of spans) {
        const labels = ticks(mar14, span).map((t) => formatAxisTime(t, span));
        expect(new Set(labels).size, `span ${span}ms → ${labels.join(" | ")}`).toBe(labels.length);
      }
    });

    it("keeps every label short enough to sit under the axis without wrapping", () => {
      // The widest tier is "Mon D HH:MM" (12 glyphs at fontSize 10); recharts
      // drops overlapping ticks, so this is about the label, not the count.
      for (const span of spans) {
        for (const t of ticks(mar14, span)) {
          expect(formatAxisTime(t, span).length).toBeLessThanOrEqual(12);
        }
      }
    });
  });
});

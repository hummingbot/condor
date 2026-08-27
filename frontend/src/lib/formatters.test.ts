import { describe, expect, it } from "vitest";

import { formatAxisCurrency, formatCompactVolume, formatCurrencyVolume } from "./formatters";

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

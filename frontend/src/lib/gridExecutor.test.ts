/**
 * The grid's price rules used to live three times inside GridConfigPanel, as
 * booleans produced after the fact. They now live here once, and one of them is
 * a clamp: a chart gesture asks for the nearest legal price *during* the drag
 * rather than being told in red text afterwards. These cover the clamp's two
 * sides and the case that makes it subtle -- an opposing price still at 0, which
 * means "not chosen yet" and must impose no bound at all.
 */

import { describe, expect, it } from "vitest";

import {
  GRID_DEFAULTS,
  clampGridPrice,
  gridConfigErrors,
  gridLineLabels,
  gridPriceErrors,
  gridPriceFieldValid,
  type GridState,
} from "@/lib/gridExecutor";

function grid(overrides: Partial<GridState> = {}): GridState {
  return { ...GRID_DEFAULTS, ...overrides };
}

describe("clampGridPrice", () => {
  describe("LONG (side 1)", () => {
    const long = grid({ side: 1, start_price: 100, end_price: 110, limit_price: 95 });

    it("leaves a price inside the range alone", () => {
      expect(clampGridPrice("start", 101, long)).toBe(101);
      expect(clampGridPrice("end", 109, long)).toBe(109);
      expect(clampGridPrice("limit", 99, long)).toBe(99);
    });

    it("keeps start strictly below end", () => {
      const clamped = clampGridPrice("start", 120, long);
      expect(clamped).toBeLessThan(110);
      expect(clamped).toBeCloseTo(110, 1);
    });

    it("keeps end strictly above start", () => {
      const clamped = clampGridPrice("end", 80, long);
      expect(clamped).toBeGreaterThan(100);
      expect(clamped).toBeCloseTo(100, 1);
    });

    it("keeps the limit strictly below start", () => {
      const clamped = clampGridPrice("limit", 105, long);
      expect(clamped).toBeLessThan(100);
      expect(clamped).toBeCloseTo(100, 1);
    });

    it("keeps start strictly above the limit", () => {
      const clamped = clampGridPrice("start", 90, long);
      expect(clamped).toBeGreaterThan(95);
    });

    it("clamps to a price the rules then accept", () => {
      const clamped = clampGridPrice("limit", 105, long);
      expect(gridPriceErrors({ ...long, limit_price: clamped })).toEqual([]);
    });
  });

  describe("SHORT (side 2)", () => {
    const short = grid({ side: 2, start_price: 100, end_price: 110, limit_price: 115 });

    it("leaves a price inside the range alone", () => {
      expect(clampGridPrice("limit", 120, short)).toBe(120);
      expect(clampGridPrice("start", 105, short)).toBe(105);
    });

    it("keeps the limit strictly above end", () => {
      const clamped = clampGridPrice("limit", 105, short);
      expect(clamped).toBeGreaterThan(110);
      expect(gridPriceErrors({ ...short, limit_price: clamped })).toEqual([]);
    });

    it("keeps end strictly below the limit", () => {
      const clamped = clampGridPrice("end", 130, short);
      expect(clamped).toBeLessThan(115);
    });

    it("does not apply the LONG rule to the limit", () => {
      // 95 is below start, which a LONG would accept and a SHORT must not.
      expect(clampGridPrice("limit", 95, short)).toBeGreaterThan(110);
    });
  });

  describe("with an opposing price still unset", () => {
    it("takes the first pick as-is on a fresh state", () => {
      const fresh = grid({ side: 1, start_price: 0, end_price: 0, limit_price: 0 });
      expect(clampGridPrice("start", 100, fresh)).toBe(100);
      expect(clampGridPrice("end", 100, fresh)).toBe(100);
      expect(clampGridPrice("limit", 100, fresh)).toBe(100);
    });

    it("bounds against a set neighbour but not an unset one", () => {
      // start picked, end and limit still 0: end is bounded, the limit is free.
      const half = grid({ side: 1, start_price: 100, end_price: 0, limit_price: 0 });
      expect(clampGridPrice("end", 50, half)).toBeGreaterThan(100);
      expect(clampGridPrice("start", 5, half)).toBe(5);
    });

    it("does not bound a SHORT limit against an unset end", () => {
      const half = grid({ side: 2, start_price: 100, end_price: 0, limit_price: 0 });
      expect(clampGridPrice("limit", 50, half)).toBe(50);
    });
  });

  it("snaps the clamped price onto the tick grid when there is one", () => {
    const state = grid({ side: 1, start_price: 100, end_price: 110, limit_price: 0 });
    const clamped = clampGridPrice("start", 120, state, 2);
    expect(clamped).toBeLessThan(110);
    expect(Number(clamped.toFixed(2))).toBe(clamped);
  });

  it("leaves a separation wide enough to survive a coarse tick", () => {
    const state = grid({ side: 1, start_price: 0, end_price: 110, limit_price: 0 });
    // Precision 0: a 1bp relative gap is far below one tick, so the tick wins.
    expect(clampGridPrice("start", 200, state, 0)).toBe(109);
  });

  it("passes a nonsense price straight through", () => {
    const state = grid({ start_price: 100, end_price: 110 });
    expect(clampGridPrice("start", 0, state)).toBe(0);
    expect(clampGridPrice("start", NaN, state)).toBeNaN();
  });
});

describe("gridPriceErrors", () => {
  it("is silent on a well-ordered LONG", () => {
    expect(gridPriceErrors(grid({ side: 1, start_price: 100, end_price: 110, limit_price: 95 }))).toEqual([]);
  });

  it("is silent on a well-ordered SHORT", () => {
    expect(gridPriceErrors(grid({ side: 2, start_price: 100, end_price: 110, limit_price: 115 }))).toEqual([]);
  });

  it("names the ordering rule before the missing prices", () => {
    const errors = gridPriceErrors(grid({ start_price: 110, end_price: 100, limit_price: 0 }));
    expect(errors).toEqual(["Lower price must be < upper price", "All prices required"]);
  });

  it("holds the limit to the side it is protecting", () => {
    expect(gridPriceErrors(grid({ side: 1, start_price: 100, end_price: 110, limit_price: 105 })))
      .toContain("LONG: limit must be < lower price");
    expect(gridPriceErrors(grid({ side: 2, start_price: 100, end_price: 110, limit_price: 105 })))
      .toContain("SHORT: limit must be > upper price");
  });
});

describe("gridLineLabels", () => {
  it("names the bounds by where they sit, not by the API field", () => {
    // start_price is the *lower* bound, so the chart must not call it "Start"
    // while the LP beside it calls its own lower bound "Lower".
    expect(gridLineLabels(1).start).toBe("Lower");
    expect(gridLineLabels(1).end).toBe("Upper");
  });

  it("names the limit for the side the stop trails on", () => {
    expect(gridLineLabels(1).limit).toBe("Lower limit");
    expect(gridLineLabels(2).limit).toBe("Upper limit");
  });
});

describe("gridConfigErrors", () => {
  const priced = { side: 1 as const, start_price: 100, end_price: 110, limit_price: 95 };

  it("adds the amount rules to the price rules", () => {
    expect(gridConfigErrors(grid({ ...priced, total_amount_quote: 0 })))
      .toEqual(["Total amount required"]);
    expect(gridConfigErrors(grid({ ...priced, total_amount_quote: 5, min_order_amount_quote: 10 })))
      .toEqual(["Total must be >= min order amount"]);
  });

  it("passes a complete config", () => {
    expect(gridConfigErrors(grid(priced))).toEqual([]);
  });
});

describe("gridPriceFieldValid", () => {
  it("settles a field only once its neighbour is set and ordered", () => {
    const half = grid({ side: 1, start_price: 100, end_price: 0, limit_price: 0 });
    expect(gridPriceFieldValid("start", half)).toBe(false);

    const full = grid({ side: 1, start_price: 100, end_price: 110, limit_price: 95 });
    expect(gridPriceFieldValid("start", full)).toBe(true);
    expect(gridPriceFieldValid("end", full)).toBe(true);
    expect(gridPriceFieldValid("limit", full)).toBe(true);
  });

  it("reads the limit against the side", () => {
    const state = grid({ side: 2, start_price: 100, end_price: 110, limit_price: 115 });
    expect(gridPriceFieldValid("limit", state)).toBe(true);
    expect(gridPriceFieldValid("limit", { ...state, side: 1 })).toBe(false);
  });
});

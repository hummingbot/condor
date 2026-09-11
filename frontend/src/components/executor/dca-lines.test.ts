/**
 * The DCA panel draws one grabbable line per level, and a level count is not a
 * fixed union — so each line names its own slot and the panel reads the level
 * back out of it. These pin that channel, and the break-even the two barriers
 * are measured from.
 */

import { describe, expect, it } from "vitest";

import { dcaBreakEven, levelIndex, levelSlot } from "./dca-config";

describe("level slots", () => {
  it("round-trips a level through its slot id", () => {
    for (const i of [0, 1, 7, 42]) expect(levelIndex(levelSlot(i))).toBe(i);
  });

  it("is the id the panel's own price field already arms", () => {
    // The chart write-back and the field's crosshair are one channel; a rename
    // on one side alone would silently stop the other from picking.
    expect(levelSlot(2)).toBe("dca_price_2");
  });

  it("does not claim a slot that is not a level's", () => {
    for (const slot of ["start", "take_profit", "stop_loss", "dca_price_", "dca_price_x"]) {
      expect(levelIndex(slot)).toBeNull();
    }
  });
});

describe("dcaBreakEven", () => {
  it("weights each level by the quote it spends", () => {
    // $100 at 100 buys 1 base, $100 at 50 buys 2 — $200 for 3 base.
    expect(dcaBreakEven([100, 50], [100, 100])).toBeCloseTo(200 / 3);
  });

  it("ignores a level that is not fully filled in", () => {
    expect(dcaBreakEven([100, 0, 50], [100, 100, 0])).toBe(100);
  });

  it("has no break-even before any level is placed", () => {
    // Which is what keeps the two barriers off a chart that has nothing to
    // measure them against.
    expect(dcaBreakEven([0, 0], [100, 100])).toBe(0);
  });
});

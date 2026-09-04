/**
 * The stop dialog must not lie about what keep_position does (CORR-307).
 *
 * For an LP executor the pool position is closed on-chain either way —
 * `lp_executor.early_stop()` moves the state to CLOSING before it looks at the
 * flag — so the old unconditional "the position stays open on the exchange"
 * told a user unwinding their range that it survived. These cases pin the
 * distinction rather than the prose: LP copy must never claim the position
 * stays open, and non-LP copy must keep saying that it does.
 */

import { describe, expect, it } from "vitest";

import { isLpExecutor, stopKeepCopy } from "./executorStopCopy";

const lp = { type: "lp" };
const grid = { type: "grid" };
const position = { type: "position" };

describe("isLpExecutor", () => {
  it("matches the lowercase-tolerant discriminator used elsewhere", () => {
    expect(isLpExecutor({ type: "lp" })).toBe(true);
    expect(isLpExecutor({ type: "LP" })).toBe(true);
    expect(isLpExecutor({ type: "position" })).toBe(false);
    expect(isLpExecutor({})).toBe(false);
    expect(isLpExecutor(null)).toBe(false);
  });
});

describe("stopKeepCopy", () => {
  it("never promises an LP pool position stays open", () => {
    const copy = stopKeepCopy([lp]);
    expect(copy.label).toBe("Keep token exposure");
    expect(copy.checked).toContain("closed on-chain");
    expect(copy.checked).toContain("position hold");
    expect(copy.checked).not.toMatch(/stays open|remain(s)? open/);
    expect(copy.unchecked).toContain("swapped back to quote");
    expect(copy.unchecked).not.toMatch(/stays open|remain(s)? open/);
  });

  it("keeps the position-stays-open wording for non-LP executors", () => {
    for (const ex of [position, grid]) {
      const copy = stopKeepCopy([ex]);
      expect(copy.label).toBe("Keep position open");
      expect(copy.checked).toBe(
        "The executor stops but the position stays open on the exchange.",
      );
      expect(copy.unchecked).toBe("The executor stops and closes any open position.");
    }
  });

  it("uses wording true of both halves for a mixed selection", () => {
    const copy = stopKeepCopy([lp, grid]);
    expect(copy.label).toBe("Keep exposure");
    // Non-LP exposure does stay open; LP pool positions do not.
    expect(copy.checked).toContain("stay open on the exchange");
    expect(copy.checked).toContain("closed on-chain");
  });

  it("falls back to the non-LP wording when nothing resolves", () => {
    expect(stopKeepCopy([]).label).toBe("Keep position open");
  });
});

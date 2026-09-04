/**
 * A dragged barrier has to survive the round trip.
 *
 * The position and DCA panels store their exits as percentages of an anchor but
 * the chart speaks prices, so every drag goes price → percentage → price. If the
 * two halves disagree the line jumps out from under the pointer, which is why
 * the round trip is the thing under test rather than either formula alone.
 */

import { describe, expect, it } from "vitest";

import {
  MAX_BARRIER_PCT,
  MIN_BARRIER_PCT,
  barrierLabel,
  barrierPct,
  barrierPrice,
} from "./barriers";

describe("barrierPrice", () => {
  it("puts a long's take profit above the entry and its stop below", () => {
    expect(barrierPrice(100, 0.02, 1, "tp")).toBeCloseTo(102);
    expect(barrierPrice(100, 0.03, 1, "sl")).toBeCloseTo(97);
  });

  it("mirrors both for a short", () => {
    expect(barrierPrice(100, 0.02, 2, "tp")).toBeCloseTo(98);
    expect(barrierPrice(100, 0.03, 2, "sl")).toBeCloseTo(103);
  });

  it("draws nothing without an anchor", () => {
    // A market entry has no entry price, and a ladder with no filled level has
    // no break-even: neither has anywhere to hang a barrier.
    expect(barrierPrice(0, 0.02, 1, "tp")).toBe(0);
  });

  it("draws nothing for a barrier switched off", () => {
    expect(barrierPrice(100, 0, 1, "sl")).toBe(0);
  });
});

describe("barrierPct", () => {
  it("round-trips a dragged price back to the same line", () => {
    for (const side of [1, 2] as const) {
      for (const kind of ["tp", "sl"] as const) {
        const target = side === 1 ? (kind === "tp" ? 104.5 : 96.25) : kind === "tp" ? 95.5 : 103.75;
        const pct = barrierPct(100, target, side, kind);
        expect(barrierPrice(100, pct, side, kind)).toBeCloseTo(target, 6);
      }
    }
  });

  it("holds a barrier dragged onto its anchor just off it", () => {
    // 0% means "disabled", which would erase the line mid-gesture and leave
    // nothing to drag back out.
    expect(barrierPct(100, 100, 1, "tp")).toBe(MIN_BARRIER_PCT);
    expect(barrierPct(100, 100, 1, "sl")).toBe(MIN_BARRIER_PCT);
  });

  it("holds a barrier dragged to the wrong side of its anchor", () => {
    // A long's take profit below the entry is not a take profit.
    expect(barrierPct(100, 90, 1, "tp")).toBe(MIN_BARRIER_PCT);
    expect(barrierPct(100, 110, 1, "sl")).toBe(MIN_BARRIER_PCT);
  });

  it("caps at the 100% the panels' own validation allows", () => {
    expect(barrierPct(100, 500, 1, "tp")).toBe(MAX_BARRIER_PCT);
  });

  it("rounds to a basis point, so the percent field can show it back", () => {
    expect(barrierPct(100, 102.123456, 1, "tp")).toBe(0.0212);
  });

  it("answers 0 without an anchor rather than dividing by it", () => {
    expect(barrierPct(0, 102, 1, "tp")).toBe(0);
  });
});

describe("barrierLabel", () => {
  it("names the barrier and its distance", () => {
    expect(barrierLabel("tp", 0.025)).toBe("TP (2.5%)");
    expect(barrierLabel("sl", 0.03)).toBe("SL (3.0%)");
  });
});

/**
 * The two PnL color contracts, pinned (READ-232).
 *
 * `pnlColor` and `pnlTextClass` encode the same rule (>= 0 green, < 0 red) for
 * two different sinks: a CSS `color` value and a Tailwind class. Both are
 * `string`, so swapping them compiles cleanly and silently renders an
 * unstyled or invisible number — TypeScript cannot catch it, so these
 * assertions stand in for it.
 */

import { describe, expect, it } from "vitest";

import { pnlColor, pnlTextClass } from "./formatters";

describe("pnlColor (CSS value form)", () => {
  it("returns a bare CSS custom-property value, never a class", () => {
    for (const val of [1, 0, -1, 1234.56, -0.0001]) {
      const out = pnlColor(val);
      expect(out).toMatch(/^var\(--color-(green|red)\)$/);
      expect(out.startsWith("text-")).toBe(false);
    }
  });

  it("maps sign to green/red with zero counting as green", () => {
    expect(pnlColor(12.5)).toBe("var(--color-green)");
    expect(pnlColor(0)).toBe("var(--color-green)");
    expect(pnlColor(-12.5)).toBe("var(--color-red)");
  });
});

describe("pnlTextClass (Tailwind class form)", () => {
  it("returns a text-color utility class, never a bare CSS value", () => {
    for (const val of [1, 0, -1, 1234.56, -0.0001]) {
      expect(pnlTextClass(val)).toMatch(/^text-\[var\(--color-(green|red)\)\]$/);
    }
  });

  it("maps sign to green/red with zero counting as green", () => {
    expect(pnlTextClass(12.5)).toBe("text-[var(--color-green)]");
    expect(pnlTextClass(0)).toBe("text-[var(--color-green)]");
    expect(pnlTextClass(-12.5)).toBe("text-[var(--color-red)]");
  });
});

describe("the two forms agree on sign and stay distinct", () => {
  it("wraps the same custom property the other returns bare", () => {
    for (const val of [7, 0, -7]) {
      expect(pnlTextClass(val)).toBe(`text-[${pnlColor(val)}]`);
      expect(pnlTextClass(val)).not.toBe(pnlColor(val));
    }
  });
});

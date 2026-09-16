/**
 * @vitest-environment jsdom
 *
 * The cached tooltip card (PERF-348).
 *
 * Both crosshair handlers used to rebuild the hover card — a ~100-line string
 * build, an `innerHTML` reparse of ~20 nodes and the forced layout of the
 * `offsetHeight` read that follows it — on every pointer move while the cursor
 * sat inside an executor box, ~60 times a second to say exactly the same thing.
 * `createOverlayTooltipView` rewrites the DOM only when one of the three
 * arguments that produced the current card changed identity.
 *
 * These tests count the writes and the layout reads on the element itself, so
 * they measure the real `renderOverlayTooltipHtml` going into the real DOM
 * rather than a stand-in. The staleness cases matter at least as much as the
 * count: a card that stops following the executor's PnL would be a worse bug
 * than the rebuild it saves.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  createOverlayTooltipView,
  type ExecutorOverlay,
  type OverlayTooltipFormatters,
} from "./executor-overlays";

function overlay(patch: Partial<ExecutorOverlay> = {}): ExecutorOverlay {
  return {
    executorId: "abc123def456",
    type: "position",
    side: "buy",
    status: "running",
    closeType: "",
    pnl: 12.5,
    pnlPct: 0.0125,
    volume: 1000,
    fees: 1.25,
    timeRange: { start: 1700000000, end: 1700003600 },
    config: {},
    ...patch,
  } as ExecutorOverlay;
}

const usd: OverlayTooltipFormatters = {
  formatValue: (v: number) => `$${v.toFixed(2)}`,
  formatPnl: (v: number) => `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`,
};

/** A tooltip div that counts the two expensive things the handler does to it. */
function makeTooltip(height = 148) {
  const el = document.createElement("div");
  const counts = { writes: 0, layouts: 0 };
  let html = "";
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (v: string) => {
      html = v;
      counts.writes += 1;
    },
    configurable: true,
  });
  Object.defineProperty(el, "offsetHeight", {
    get: () => {
      counts.layouts += 1;
      // A hidden card measures 0 in the browser too — that is what the view's
      // 200 fallback exists for.
      return el.style.display === "none" ? 0 : height;
    },
    configurable: true,
  });
  return { el, counts, html: () => html };
}

describe("createOverlayTooltipView", () => {
  let view: ReturnType<typeof createOverlayTooltipView>;

  beforeEach(() => {
    view = createOverlayTooltipView();
  });

  it("builds the card once for a hover that never leaves the same overlay", () => {
    const { el, counts } = makeTooltip();
    const o = overlay();

    const heights = Array.from({ length: 50 }, () => view.show(el, o, usd));

    // 50 crosshair moves, one card build and one forced layout.
    expect(counts.writes).toBe(1);
    expect(counts.layouts).toBe(1);
    expect(new Set(heights)).toEqual(new Set([148]));
    expect(el.style.display).toBe("block");
  });

  it("swaps the card on the first move that changes the winning overlay", () => {
    const { el, counts, html } = makeTooltip();
    const a = overlay({ executorId: "aaaaaaaaaa11" });
    const b = overlay({ executorId: "bbbbbbbbbb22" });

    view.show(el, a, usd);
    view.show(el, a, usd);
    expect(html()).toContain("aaaaaaaaaa");

    view.show(el, b, usd);
    expect(counts.writes).toBe(2);
    expect(html()).toContain("bbbbbbbbbb");
    expect(html()).not.toContain("aaaaaaaaaa");

    // ...and back again, on the first move that swings the hit test back.
    view.show(el, a, usd);
    expect(counts.writes).toBe(3);
    expect(html()).toContain("aaaaaaaaaa");
  });

  it("repaints when a refetch lands a new PnL on the same executor", () => {
    const { el, counts, html } = makeTooltip();

    view.show(el, overlay({ pnl: 12.5 }), usd);
    expect(html()).toContain("+$12.50");

    // react-query's structural sharing hands the overlay a new identity exactly
    // when one of its values actually changed, so this is what a refetch does.
    for (let i = 0; i < 10; i++) view.show(el, overlay({ pnl: 41.25 }), usd);

    expect(html()).toContain("+$41.25");
    expect(html()).not.toContain("+$12.50");
    // One rebuild for the first card, one per new overlay object after it.
    expect(counts.writes).toBe(11);
  });

  it("reprints in the new currency when the display formatters change", () => {
    const { el, counts, html } = makeTooltip();
    const o = overlay();
    const eur: OverlayTooltipFormatters = {
      formatValue: (v: number) => `€${v.toFixed(2)}`,
      formatPnl: (v: number) => `${v >= 0 ? "+" : ""}€${v.toFixed(2)}`,
    };

    view.show(el, o, usd);
    expect(html()).toContain("+$12.50");

    view.show(el, o, eur);
    view.show(el, o, eur);
    expect(counts.writes).toBe(2);
    expect(html()).toContain("+€12.50");
  });

  it("re-renders after the pointer leaves the pane and comes back to the same overlay", () => {
    const { el, counts, html } = makeTooltip();
    const o = overlay();

    view.show(el, o, usd);
    view.hide(el);
    expect(el.style.display).toBe("none");

    view.show(el, o, usd);
    expect(counts.writes).toBe(2);
    expect(el.style.display).toBe("block");
    expect(html()).toContain("abc123def4");
  });

  it("keeps the flip-and-clamp maths honest: the reused height is the measured one", () => {
    const { el } = makeTooltip(312);
    const o = overlay();

    // First move measures; every later move must position against that same
    // number, not against the 200 fallback or a zero.
    expect(view.show(el, o, usd)).toBe(312);
    expect(view.show(el, o, usd)).toBe(312);
    expect(view.show(el, o, usd)).toBe(312);
  });

  it("falls back to 200 when the card cannot be measured", () => {
    const { el } = makeTooltip(0);
    expect(view.show(el, overlay(), usd)).toBe(200);
  });

  it("measures per element, so a second tooltip node gets its own card", () => {
    const first = makeTooltip(100);
    const second = makeTooltip(260);
    const o = overlay();

    expect(view.show(first.el, o, usd)).toBe(100);
    expect(view.show(second.el, o, usd)).toBe(260);
    expect(second.counts.writes).toBe(1);
    expect(second.html()).toContain("abc123def4");
  });
});

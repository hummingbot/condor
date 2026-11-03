/**
 * @vitest-environment jsdom
 *
 * The executor hover card, pinned (ARCH-286).
 *
 * `renderOverlayTooltipHtml` is the one card both the trade chart and the
 * executor chart draw; it used to be two copies that had drifted apart in four
 * directions. These tests pin what the merged version has to keep:
 *
 *  - the badges and rows a buy and a sell each produce;
 *  - the LP/range branch, which only one of the two copies ever had;
 *  - `formatPriceSig`'s ladder for every price, rather than a re-hand-rolled one;
 *  - the injected display-currency formatters, rather than hardcoded dollars;
 *  - and, above all, that every backend-supplied string stays HTML-escaped
 *    (SEC-018) — the card is built as an HTML string and assigned to
 *    `innerHTML`, so an unescaped field is script execution, not a typo.
 *
 * jsdom loads no stylesheet, so `getThemeColors()` resolves each CSS variable
 * to its documented fallback. That is what makes the colours here assertable.
 */

import { describe, expect, it } from "vitest";

import { renderOverlayTooltipHtml, type ExecutorOverlay } from "./executor-overlays";

const fmt = {
  formatValue: (v: number) => `€${v.toFixed(2)}`,
  formatPnl: (v: number) => `${v >= 0 ? "+" : ""}€${v.toFixed(2)}`,
};

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
    priceLines: [],
    markers: [],
    timeRange: { start: 1, end: 2 },
    entryPrice: 1234.5678,
    exitPrice: 1250,
    ...patch,
  };
}

/** Parse the card so assertions can ask the DOM, not a substring. */
function parse(html: string): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = html;
  return host;
}

describe("renderOverlayTooltipHtml", () => {
  it("describes a buy position", () => {
    const html = renderOverlayTooltipHtml(overlay(), fmt);
    const text = parse(html).textContent ?? "";

    expect(text).toContain("abc123def4");
    expect(text).toContain("buy");
    expect(text).toContain("running");
    expect(text).toContain("POSITION");
    // Injected formatters, not dollars.
    expect(text).toContain("+€12.50");
    expect(text).toContain("€1000.00");
    expect(text).toContain("€1.25");
    expect(text).toContain("+1.25%");
    // A running executor's far price is "Current", not "Close".
    expect(text).toContain("Current");
    expect(text).not.toContain("Close");
    // The up colour, and the green status pill of an active executor.
    expect(html).toContain("#22c55e");
  });

  it("describes a closed sell position", () => {
    const html = renderOverlayTooltipHtml(
      overlay({ side: "sell", status: "completed", closeType: "TP", pnl: -8, pnlPct: -0.02 }),
      fmt,
    );
    const text = parse(html).textContent ?? "";

    expect(text).toContain("sell");
    expect(text).toContain("completed");
    expect(text).toContain("TP");
    expect(text).toContain("€-8.00");
    expect(text).toContain("-2.00%");
    // Not running, so the far price is the close.
    expect(text).toContain("Close");
    expect(html).toContain("#ef4444");
  });

  it("gives an LP overlay a neutral range badge and its bounds", () => {
    const html = renderOverlayTooltipHtml(
      overlay({
        type: "lp",
        side: "sell", // normSide files a two-sided range under "sell"
        gridBox: { startTime: 1, endTime: 2, startPrice: 260, endPrice: 180, color: "#fff" },
        config: { lp_provider: "meteora", keep_position: true },
      }),
      fmt,
    );
    const text = parse(html).textContent ?? "";

    // A range has no direction: neutral label, neutral colour, never "sell".
    expect(text).toContain("range");
    expect(text).not.toContain("sell");
    expect(html).toContain("#9ca3af");
    // startPrice is the box's upper edge (see computeLpOverlay).
    expect(text).toContain("Upper Price");
    expect(text).toContain("260.0000");
    expect(text).toContain("Lower Price");
    expect(text).toContain("180.0000");
    expect(text).toContain("Provider");
    expect(text).toContain("meteora");
    expect(text).toContain("Keep Position");
    expect(text).toContain("Yes");
  });

  it("labels a grid overlay's box by its bounds", () => {
    const html = renderOverlayTooltipHtml(
      overlay({
        type: "grid",
        gridBox: { startTime: 1, endTime: 2, startPrice: 100, endPrice: 120, limitPrice: 95, color: "#fff" },
      }),
      fmt,
    );
    const text = parse(html).textContent ?? "";

    expect(text).toContain("Start Price");
    expect(text).toContain("End Price");
    expect(text).toContain("Limit Price");
    expect(text).not.toContain("Upper Price");
  });

  it("prices every row with the canonical significant-digit ladder", () => {
    const text = parse(
      renderOverlayTooltipHtml(overlay({ entryPrice: 0.0000123456789, exitPrice: 12345.678 }), fmt),
    ).textContent ?? "";

    // formatPriceSig: below 1 → toPrecision(6), at/above 1000 → toFixed(2).
    expect(text).toContain("0.0000123457");
    expect(text).toContain("12345.68");
  });

  it("renders config rows from the triple barrier, whether object or JSON string", () => {
    const asObject = parse(
      renderOverlayTooltipHtml(
        overlay({ config: { triple_barrier_config: { take_profit: 0.03 }, stop_loss: 0.01, leverage: 5 } }),
        fmt,
      ),
    ).textContent ?? "";
    const asString = parse(
      renderOverlayTooltipHtml(
        overlay({ config: { triple_barrier_config: '{"take_profit": 0.03}', stop_loss: 0.01, leverage: 5 } }),
        fmt,
      ),
    ).textContent ?? "";

    for (const text of [asObject, asString]) {
      expect(text).toContain("Take Profit");
      expect(text).toContain("3.00%");
      expect(text).toContain("Stop Loss");
      expect(text).toContain("1.00%");
      expect(text).toContain("Leverage");
      expect(text).toContain("5x");
    }
  });

  it("converts the notional with the injected formatter, not with dollars", () => {
    const text = parse(
      renderOverlayTooltipHtml(overlay({ config: { total_amount_quote: 250 } }), fmt),
    ).textContent ?? "";

    expect(text).toContain("Amount");
    expect(text).toContain("€250.00");
    expect(text).not.toContain("$");
  });

  it("reads its colours from the theme rather than hardcoding the dark palette", () => {
    // The card sits on `--color-surface`, which is white in the light theme, so
    // a white-on-white separator or chip baked into the markup is a real defect.
    const html = renderOverlayTooltipHtml(overlay({ closeType: "TP" }), fmt);
    expect(html).not.toContain("rgba(255,255,255");
    // Separators and the type chip use --color-border (jsdom fallback #1c2541).
    expect(html).toContain("#1c2541");
  });

  it("escapes every backend string it interpolates", () => {
    const hostile = '"><img src=x onerror=alert(1)>';
    const html = renderOverlayTooltipHtml(
      overlay({
        executorId: hostile,
        type: hostile,
        status: hostile,
        closeType: hostile,
        config: { lp_provider: hostile, amount: hostile },
        gridBox: { startTime: 1, endTime: 2, startPrice: 1, endPrice: 2, color: "#fff" },
      }),
      fmt,
    );

    // Nothing injected became markup: no element the card does not define, and
    // no attribute broken out of.
    const host = parse(html);
    expect(host.querySelector("img")).toBeNull();
    const benign = parse(renderOverlayTooltipHtml(overlay({
      executorId: "x", type: "x", status: "x", closeType: "x",
      gridBox: { startTime: 1, endTime: 2, startPrice: 1, endPrice: 2, color: "#fff" },
      config: { lp_provider: "x", amount: "y" },
    }), fmt));
    expect(host.querySelectorAll("*").length).toBe(benign.querySelectorAll("*").length);

    // The payload survives as inert text rather than being dropped: both the
    // tag delimiters and the quote that would break out of a style attribute
    // are entity-encoded.
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("&quot;&gt;&lt;img");
    expect(html).not.toContain("<img");
  });

  it("escapes what the injected formatters return", () => {
    // The formatters are supplied by the caller and fed backend numbers; the
    // card does not get to assume their output is markup-safe either.
    const html = renderOverlayTooltipHtml(overlay(), {
      formatValue: () => "<b>vol</b>",
      formatPnl: () => "<b>pnl</b>",
    });

    expect(parse(html).querySelector("b")).toBeNull();
    expect(html).toContain("&lt;b&gt;");
  });
});

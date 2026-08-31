/**
 * Click-to-set reports the price under the pointer (CORR-263).
 *
 * The crosshair runs in `Normal` mode, so the horizontal line and the price-axis
 * label follow the pointer freely — the user reads a price off the axis and
 * clicks it. The pick used to hand back the hovered candle's `close` instead,
 * which is one price for the whole column: every click inside a candle gave the
 * same number regardless of height, and only the empty margin past the last
 * candle appeared to work. These tests drive the real crosshair callback the
 * component subscribes and assert the click reports `coordinateToPrice(y)`.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { PickSlot } from "@/components/executor/types";
import { TradeChart } from "./TradeChart";

/** Pixel row → price, steep enough that two clicks 60px apart cannot collide. */
const PRICE_AT_Y_200 = 105234.87313432835;
function priceAtY(y: number): number {
  return PRICE_AT_Y_200 - (y - 200) * 10;
}

const chartState = vi.hoisted(() => ({
  series: null as unknown,
  crosshairCb: null as ((param: unknown) => void) | null,
}));

vi.mock("lightweight-charts", () => {
  /**
   * The chart pieces the component reaches for are many and mostly irrelevant
   * here, so unknown members answer with a no-op spy; only the price mapping and
   * the crosshair subscription carry real behaviour.
   */
  const stub = (own: Record<string, unknown>) =>
    new Proxy(own, {
      get(target, prop) {
        if (typeof prop !== "string" || prop === "then") return undefined;
        if (!(prop in target)) target[prop] = vi.fn();
        return target[prop];
      },
    });

  const series = stub({
    coordinateToPrice: vi.fn((y: number) => priceAtY(y)),
    priceToCoordinate: vi.fn(() => 0),
    createPriceLine: vi.fn(() => ({})),
  });
  chartState.series = series;

  const timeScale = stub({});
  const chart = stub({
    addSeries: vi.fn(() => series),
    timeScale: vi.fn(() => timeScale),
    subscribeCrosshairMove: vi.fn((cb: (param: unknown) => void) => {
      chartState.crosshairCb = cb;
    }),
  });

  return {
    createChart: vi.fn(() => chart),
    CandlestickSeries: {},
    LineSeries: {},
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  };
});

vi.mock("@/hooks/useCandleStore", () => ({
  useCandleStore: () => ({
    candles: [],
    isStale: false,
    mergeCandles: vi.fn(),
    setDuration: vi.fn(),
  }),
}));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { getCandles: vi.fn(async () => []) },
}));

vi.mock("@/lib/candle-store", () => ({
  candleChannelKey: (...parts: unknown[]) => parts.join(":"),
  candleStore: { onUpdate: vi.fn(() => () => {}) },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let onPriceSet: Mock<(field: PickSlot, price: number) => void>;

/** The bar the crosshair sits on — its close is nowhere near the pointer's price. */
const HOVERED_BAR = { open: 1, high: 2, low: 0.5, close: 99000 };

async function render(pricePrecision?: number) {
  await act(async () => {
    root.render(
      <TradeChart
        server="local"
        connector="binance"
        pair="BTC-USDT"
        interval="1m"
        lookbackSeconds={3600}
        startPrice={100}
        endPrice={200}
        limitPrice={150}
        side={1}
        minSpread={0.001}
        activePickField="start"
        onPriceSet={onPriceSet}
        pricePrecision={pricePrecision}
      />,
    );
  });
}

/** Move the crosshair to a pixel row, optionally over a candle. */
async function moveTo(y: number, overBar = true) {
  const seriesData = new Map<unknown, unknown>();
  if (overBar) seriesData.set(chartState.series, HOVERED_BAR);
  await act(async () => {
    chartState.crosshairCb?.({
      point: { x: 300, y },
      seriesData,
      time: overBar ? 1_700_000_000 : undefined,
    });
  });
}

/** The pane the click handler is bound to. */
function pane(): HTMLDivElement {
  return container.querySelector(".absolute.inset-0") as HTMLDivElement;
}

async function click(shiftKey = false) {
  await act(async () => {
    pane().dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  chartState.crosshairCb = null;
  onPriceSet = vi.fn<(field: PickSlot, price: number) => void>();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("TradeChart click-to-set", () => {
  it("reports the price under the pointer, not the hovered candle's close", async () => {
    // Precision 8 so the assertion is against the coordinate price itself
    // rather than the rounding, which has its own unit test.
    await render(8);
    await moveTo(200);
    await click();

    expect(onPriceSet).toHaveBeenCalledTimes(1);
    const [field, price] = onPriceSet.mock.calls[0];
    expect(field).toBe("start");
    expect(price).not.toBe(HOVERED_BAR.close);
    expect(price).toBeCloseTo(priceAtY(200), 4);
  });

  it("gives two different prices for two heights inside the same candle", async () => {
    await render(8);
    await moveTo(200);
    await click();
    await moveTo(260);
    await click();

    expect(onPriceSet).toHaveBeenCalledTimes(2);
    const first = onPriceSet.mock.calls[0][1];
    const second = onPriceSet.mock.calls[1][1];
    expect(first).not.toBe(second);
    expect(first).toBeCloseTo(priceAtY(200), 4);
    expect(second).toBeCloseTo(priceAtY(260), 4);
  });

  it("still yields a usable price in the empty area past the last candle", async () => {
    await render(8);
    await moveTo(240, false);
    await click();

    expect(onPriceSet).toHaveBeenCalledTimes(1);
    expect(onPriceSet.mock.calls[0][1]).toBeCloseTo(priceAtY(240), 4);
  });

  it("rounds to the venue's price precision", async () => {
    await render(2);
    await moveTo(200);
    await click();

    expect(onPriceSet).toHaveBeenCalledWith("start", 105234.87);
  });

  it("leaves the measure tool's shift+click alone", async () => {
    await render();
    await moveTo(200);
    await click(true);

    expect(onPriceSet).not.toHaveBeenCalled();
  });
});

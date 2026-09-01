/**
 * The REST candle backfill survives StrictMode's double mount (CORR-289).
 *
 * The effect cancels its in-flight fetch on cleanup, so a mount/unmount/mount —
 * exactly what StrictMode does in development — throws the first fetch away and
 * must issue a second one. A ref remembering the last key defeated that: the
 * second pass matched the ref the first pass wrote and returned early, so the
 * chart merged nothing and every dev chart drew only what the WS could offer.
 *
 * The production contract is the other half: one backfill per distinct set of
 * the effect's six dependencies, and no refetch when the props do not change.
 *
 * @vitest-environment jsdom
 */

import { act, StrictMode, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TradeChart } from "./TradeChart";

const CANDLES = [
  { timestamp: 1_700_000_000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
  { timestamp: 1_700_000_060, open: 1.5, high: 2.5, low: 1, close: 2, volume: 12 },
];

const store = vi.hoisted(() => ({
  mergeCandles: vi.fn(),
  setDuration: vi.fn(),
}));

const apiState = vi.hoisted(() => ({
  getCandles: vi.fn(),
}));

vi.mock("lightweight-charts", () => {
  /** Only the shape the component walks matters here; the rest answers no-ops. */
  const stub = (own: Record<string, unknown>) =>
    new Proxy(own, {
      get(target, prop) {
        if (typeof prop !== "string" || prop === "then") return undefined;
        if (!(prop in target)) target[prop] = vi.fn();
        return target[prop];
      },
    });

  const series = stub({
    coordinateToPrice: vi.fn(() => 100),
    priceToCoordinate: vi.fn(() => 0),
    createPriceLine: vi.fn(() => ({})),
  });
  const chart = stub({
    addSeries: vi.fn(() => series),
    timeScale: vi.fn(() => stub({})),
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
    mergeCandles: store.mergeCandles,
    setDuration: store.setDuration,
  }),
}));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { getCandles: (...args: unknown[]) => apiState.getCandles(...args) },
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

const PROPS: ComponentProps<typeof TradeChart> = {
  server: "local",
  connector: "binance",
  pair: "BTC-USDT",
  interval: "1m",
  lookbackSeconds: 3600,
  startPrice: 100,
  endPrice: 200,
  limitPrice: 150,
  side: 1,
  minSpread: 0.001,
  activePickField: null,
  onPriceSet: vi.fn(),
};

/** Render the chart, optionally wrapped the way `main.tsx` wraps the app. */
async function render(strict: boolean, extra: Partial<ComponentProps<typeof TradeChart>> = {}) {
  const tree = <TradeChart {...PROPS} {...extra} />;
  await act(async () => {
    root.render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  });
  // The fetch resolves a microtask after the effect ran; let its `.then` land.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  store.mergeCandles.mockClear();
  store.setDuration.mockClear();
  apiState.getCandles.mockReset();
  apiState.getCandles.mockResolvedValue(CANDLES);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("TradeChart REST backfill", () => {
  it("merges the fetched candles when StrictMode mounts the effect twice", async () => {
    await render(true);

    expect(apiState.getCandles).toHaveBeenCalled();
    expect(store.mergeCandles).toHaveBeenCalledWith(CANDLES);
  });

  it("merges the fetched candles on a single mount", async () => {
    await render(false);

    expect(apiState.getCandles).toHaveBeenCalledTimes(1);
    expect(store.mergeCandles).toHaveBeenCalledWith(CANDLES);
  });

  it("does not refetch while the six dependencies stay the same", async () => {
    await render(false);
    expect(apiState.getCandles).toHaveBeenCalledTimes(1);

    await render(false, { limitPrice: 151 });

    expect(apiState.getCandles).toHaveBeenCalledTimes(1);
  });

  it("refetches when a dependency changes", async () => {
    await render(false);
    await render(false, { interval: "5m" });

    expect(apiState.getCandles).toHaveBeenCalledTimes(2);
    expect(apiState.getCandles).toHaveBeenLastCalledWith(
      "local",
      "binance",
      "BTC-USDT",
      "5m",
      5000,
      expect.any(Number),
      undefined,
      undefined,
    );
  });
});

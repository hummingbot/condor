/**
 * The chart draws the range its picker names, not every candle the store holds.
 *
 * The candle store is shared by every chart on a channel and never trims to one
 * chart's range: once a 1-day chart has loaded, the channel holds a day of
 * candles for a 1-hour chart too. The chart drew all of them, and only ever
 * redrew when older history arrived — so picking a shorter range after a longer
 * one left the longer history on screen.
 *
 * @vitest-environment jsdom
 */

import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { CandleData } from "@/lib/api";
import { chartDouble } from "@/test/lightweight-charts-double";
import { TradeChart } from "./TradeChart";

const HOUR = 3600;
const DAY = 24 * HOUR;
const NEWEST = 1_700_086_400;

/** `count` 1m candles ending at `newest`, oldest first — the store's order. */
function minutes(count: number, newest = NEWEST): CandleData[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: newest - (count - 1 - i) * 60,
    open: 1,
    high: 2,
    low: 0.5,
    close: 1.5,
    volume: 1,
  }));
}

/** What a 1-day chart leaves in the shared store. */
const A_DAY = minutes(1440);

const store = vi.hoisted(() => ({ candles: [] as CandleData[] }));

vi.mock("@/hooks/useCandleStore", () => ({
  useCandleStore: () => ({
    candles: store.candles,
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

type Listener = (candles: CandleData[]) => void;
/** The chart's own store listener, so a test can push a frame the way the WS does. */
const live = vi.hoisted(() => ({ listener: null as Listener | null }));

vi.mock("@/lib/candle-store", () => ({
  candleChannelKey: (...parts: unknown[]) => parts.join(":"),
  candleStore: {
    onUpdate: vi.fn((_key: string, cb: Listener) => {
      live.listener = cb;
      return () => {};
    }),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

const PROPS: ComponentProps<typeof TradeChart> = {
  server: "local",
  connector: "binance_perpetual",
  pair: "BTC-USDT",
  interval: "1m",
  lookbackSeconds: HOUR,
  startPrice: 100,
  endPrice: 200,
  limitPrice: 150,
  side: 1,
  minSpread: 0.001,
  activePickField: null,
  onPriceSet: vi.fn(),
};

async function render(lookbackSeconds: number) {
  await act(async () => {
    root.render(<TradeChart {...PROPS} lookbackSeconds={lookbackSeconds} />);
  });
  // The chart library arrives through a dynamic import a few hops out; give it
  // a macrotask so the series exists and the data effect has run.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** The bar times of every full `setData()` so far, oldest render first. */
function draws(): number[][] {
  const setData = chartDouble.series!.setData as Mock;
  return setData.mock.calls.map(([bars]) => (bars as { time: number }[]).map((b) => b.time));
}

/** The bar times of every incremental `update()` so far, in call order. */
function updates(): number[] {
  const update = chartDouble.series!.update as Mock;
  return update.mock.calls.map(([bar]) => (bar as { time: number }).time);
}

function fits(): number {
  return (chartDouble.timeScale!.fitContent as Mock).mock.calls.length;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  chartDouble.reset();
  store.candles = A_DAY;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("TradeChart range", () => {
  it("draws only the picked range when the store holds more", async () => {
    await render(HOUR);

    const [drawn] = draws().slice(-1);
    expect(drawn).toHaveLength(61);
    expect(drawn[0]).toBe(NEWEST - HOUR);
    expect(drawn[drawn.length - 1]).toBe(NEWEST);
  });

  it("redraws to a shorter range picked after a longer one", async () => {
    await render(DAY);
    expect(draws().slice(-1)[0]).toHaveLength(1440);

    await render(HOUR);

    expect(draws()).toHaveLength(2);
    expect(draws()[1]).toHaveLength(61);
    expect(draws()[1][0]).toBe(NEWEST - HOUR);
    expect(fits()).toBe(2);
  });

  it("redraws back out to a longer range the store already holds", async () => {
    await render(HOUR);
    await render(DAY);

    expect(draws()).toHaveLength(2);
    expect(draws()[1]).toHaveLength(1440);
  });

  it("leaves a live tick to the incremental update", async () => {
    await render(HOUR);
    expect(draws()).toHaveLength(1);

    // A newer bar appends and the window slides one bar forward with it.
    store.candles = minutes(1441, NEWEST + 60);
    await render(HOUR);

    expect(draws()).toHaveLength(1);
    expect(fits()).toBe(1);
  });
});

describe("TradeChart live frames (FP-03)", () => {
  it("draws every candle of a multi-candle frame, oldest first", async () => {
    await render(DAY);
    expect(draws()).toHaveLength(1);

    // What a reconnect delivers: the buffer again, with five bars the chart
    // never saw on the end of it.
    act(() => live.listener!(minutes(1445, NEWEST + 5 * 60)));

    // The last drawn bar is re-applied too — it may have closed at a different
    // price than the tick the chart last saw.
    expect(updates()).toEqual([0, 1, 2, 3, 4, 5].map((n) => NEWEST + n * 60));
    expect(draws()).toHaveLength(1);
  });

  it("keeps drawing after a batch: the next frame starts from the new last bar", async () => {
    await render(DAY);
    act(() => live.listener!(minutes(1443, NEWEST + 3 * 60)));
    (chartDouble.series!.update as Mock).mockClear();

    act(() => live.listener!(minutes(1444, NEWEST + 4 * 60)));

    expect(updates()).toEqual([NEWEST + 3 * 60, NEWEST + 4 * 60]);
  });

  it("redraws once, without refitting, when the gap is long", async () => {
    await render(DAY);

    act(() => live.listener!(minutes(1440 + 120, NEWEST + 120 * 60)));

    expect(updates()).toEqual([]);
    expect(draws()).toHaveLength(2);
    expect(draws()[1][draws()[1].length - 1]).toBe(NEWEST + 120 * 60);
    expect(fits()).toBe(1);
  });

  it("redraws when a bar behind the last drawn one changed", async () => {
    await render(DAY);

    const frame = minutes(1440);
    frame[1000] = { ...frame[1000], close: 9 };
    act(() => live.listener!(frame));

    expect(updates()).toEqual([]);
    expect(draws()).toHaveLength(2);
    expect(fits()).toBe(1);
  });

  it("ignores a frame older than everything drawn", async () => {
    await render(HOUR);

    act(() => live.listener!(minutes(10, NEWEST - 2 * DAY)));

    expect(updates()).toEqual([]);
    expect(draws()).toHaveLength(1);
  });
});

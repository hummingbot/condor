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
 * The same double mount fires the chart-init effect twice in one tick, so
 * `import("lightweight-charts")` is requested twice concurrently — and a
 * per-file `vi.mock` answered the stub to the first request and raw-imported
 * the real library for the second. It is the concurrency, not StrictMode: two
 * plain mounts in one `act()` split the same way, and neither a static nor an
 * awaited warm-up import of the module changes it. The real `ChartWidget` then
 * builds against a jsdom that has no 2D canvas and schedules a draw frame,
 * which fired after teardown, outside every test, and exited the whole suite 1
 * with "2 unhandled errors" while every test reported green (CORR-360).
 *
 * The library is now substituted at the resolver, so both requests get the same
 * double and no real widget can be built at all (CORR-368). This file keeps its
 * own frame queue and 2D context anyway: they are what proves the StrictMode
 * mount leaves nothing behind, and they cost nothing.
 *
 * @vitest-environment jsdom
 */

import { act, StrictMode, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { chartDouble, createChart } from "@/test/lightweight-charts-double";
import { TradeChart } from "./TradeChart";

const CANDLES = [
  { timestamp: 1_700_000_000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
  { timestamp: 1_700_000_060, open: 1.5, high: 2.5, low: 1, close: 2, volume: 12 },
];

const store = vi.hoisted(() => ({
  mergeCandles: vi.fn(),
  setDuration: vi.fn(),
  /** What the channel holds, and whether its feed has gone quiet. */
  candles: [] as { timestamp: number; open: number; high: number; low: number; close: number; volume: number }[],
  isStale: false,
}));

const apiState = vi.hoisted(() => ({
  getCandles: vi.fn(),
}));

vi.mock("@/hooks/useCandleStore", () => ({
  useCandleStore: () => ({
    candles: store.candles,
    isStale: store.isStale,
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

/** The double's factory, read back to prove the component reached the chart. */
const createChartStub = createChart as unknown as Mock;

/** Frames the render scheduled, so teardown can prove none outlive the tree. */
const pendingFrames = new Map<number, FrameRequestCallback>();

/**
 * jsdom ships no canvas backend, and asking it for one only prints
 * "Not implemented: HTMLCanvasElement's getContext()" and answers null — which a
 * real chart widget dereferences. The calls just have to answer; this window
 * belongs to this file alone, so it is patched once and never restored.
 */
HTMLCanvasElement.prototype.getContext = (() =>
  new Proxy({} as Record<string, unknown>, {
    get(target, prop) {
      if (typeof prop !== "string") return undefined;
      if (!(prop in target)) target[prop] = vi.fn(() => ({ width: 0 }));
      return target[prop];
    },
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;

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
  // The chart's own dynamic import settles a few hops further out, so give the
  // whole chain a macrotask: the chart is then built and read inside the test
  // rather than somewhere after it.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  // Both paths ask for the chart library — the StrictMode one twice at once —
  // and the resolver hands the double to every request, so the component got a
  // chart and jsdom got no canvas. The module identity is a result here, not an
  // assumption, and it holds on the concurrent path too.
  expect(createChartStub).toHaveBeenCalled();
  expect(container.querySelector("canvas")).toBeNull();
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
  // Own the frame queue instead of letting jsdom fire it off a timer: a frame
  // that runs after its test is an exception belonging to no test at all.
  let nextFrame = 1;
  pendingFrames.clear();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextFrame++;
    pendingFrames.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    pendingFrames.delete(id);
  });
  // Discards the charts the last test built and reinstates the price scale.
  chartDouble.reset({ toPrice: () => 100 });
  store.mergeCandles.mockClear();
  store.setDuration.mockClear();
  store.candles = [];
  store.isStale = false;
  apiState.getCandles.mockReset();
  apiState.getCandles.mockResolvedValue(CANDLES);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  // Unmounting destroys the chart, which cancels its own frame, so the queue has
  // to be empty. Drain it either way, then report what was still queued.
  const queued = [...pendingFrames.keys()];
  pendingFrames.clear();
  expect(queued).toEqual([]);
  vi.unstubAllGlobals();
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

/**
 * The backfill reaches only as far back as the chart will keep (FP-02).
 *
 * 1m × 30d asked upstream for 43,200 candles and the store evicted 95% of them
 * on arrival. The pages step the interval up before it comes to that; a chart
 * handed a too-fine interval anyway clamps the request and says so.
 */
describe("TradeChart range vs the candle budget", () => {
  const DAY = 86400;
  const startOf = (call: unknown[]) => call[5] as number;

  it("asks for the whole range when it fits the budget", async () => {
    const before = Math.floor(Date.now() / 1000);
    await render(false, { lookbackSeconds: 3 * DAY });

    expect(store.setDuration).toHaveBeenLastCalledWith(3 * DAY);
    const start = startOf(apiState.getCandles.mock.calls[0]);
    expect(before - start).toBeGreaterThanOrEqual(3 * DAY);
    expect(before - start).toBeLessThan(3 * DAY + 5);
    expect(container.querySelector("[data-range-clamped]")).toBeNull();
  });

  it("clamps a 30-day range of 1m candles to the week it can keep, and says so", async () => {
    const before = Math.floor(Date.now() / 1000);
    await render(false, { lookbackSeconds: 30 * DAY });

    expect(store.setDuration).toHaveBeenLastCalledWith(7 * DAY);
    const start = startOf(apiState.getCandles.mock.calls[0]);
    expect(before - start).toBeLessThan(7 * DAY + 5);
    expect(container.querySelector("[data-range-clamped]")?.textContent).toContain("7d of 30d at 1m");
  });

  it("does not clamp the same range at an interval that fits it", async () => {
    await render(false, { lookbackSeconds: 30 * DAY, interval: "5m" });

    expect(store.setDuration).toHaveBeenLastCalledWith(30 * DAY);
    expect(container.querySelector("[data-range-clamped]")).toBeNull();
  });
});

/**
 * The chart says what state its feed is in (FP-05).
 *
 * "No candles for this market", "the history request failed", "still loading"
 * and "the feed stopped" all used to be the same pane: an empty chart, or a
 * chart that looked live. The signals existed — the backfill's outcome, the
 * store's staleness timer — and were thrown away.
 */
describe("TradeChart feed state", () => {
  const feed = () => container.querySelector<HTMLElement>("[data-feed-state]");
  const retryButton = () =>
    [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Retry");

  it("says it is loading until the history request answers", async () => {
    apiState.getCandles.mockReturnValue(new Promise(() => {}));
    await render(false);

    expect(feed()?.dataset.feedState).toBe("loading");
    expect(feed()?.textContent).toContain("Loading candles");
  });

  it("says there are no candles when the request answers with none", async () => {
    apiState.getCandles.mockResolvedValue([]);
    await render(false);

    expect(feed()?.dataset.feedState).toBe("empty");
    expect(feed()?.textContent).toContain("No 1m candles for BTC-USDT");
  });

  it("shows an error with Retry after three failed requests, and Retry asks again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    apiState.getCandles.mockRejectedValue(new Error("upstream down"));
    await act(async () => {
      root.render(<TradeChart {...PROPS} />);
    });
    // Three tries, 2 s and 4 s apart.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(7000);
    });

    expect(apiState.getCandles).toHaveBeenCalledTimes(3);
    expect(feed()?.dataset.feedState).toBe("error");
    expect(feed()?.textContent).toContain("Could not load candles");

    apiState.getCandles.mockResolvedValue([]);
    await act(async () => {
      retryButton()!.click();
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(apiState.getCandles).toHaveBeenCalledTimes(4);
    expect(feed()?.dataset.feedState).toBe("empty");
  });

  it("flags a stalled feed over a chart that has candles", async () => {
    store.candles = CANDLES;
    store.isStale = true;
    await render(false);

    expect(feed()?.dataset.feedState).toBe("stale");
    expect(feed()?.textContent).toContain("Feed stalled");
  });

  it("says nothing over a live chart", async () => {
    store.candles = CANDLES;
    await render(false);

    expect(feed()).toBeNull();
  });
});

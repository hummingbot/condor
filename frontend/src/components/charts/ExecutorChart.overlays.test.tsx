/**
 * The overlay series follow the drawing, not the prop's identity (PERF-284).
 *
 * Every caller hands this chart a fresh `executors` array: the detail panel
 * mints `[executor]` per render while a divider drag renders it on every
 * `mousemove`, and `useAgentExecutors` returns a new filtered array on every 2s
 * `executors:<server>` frame. The overlay effect was keyed on that identity, so
 * each of those renders removed and re-added every line series for a picture
 * that had not changed. These tests count `addSeries`/`removeSeries` across
 * re-renders: unchanged content must cost nothing, changed content must redraw.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExecutorInfo } from "@/lib/api";
// The library itself is substituted at the resolver, for every test file at
// once (CORR-368) — `chartDouble` is only how this file reads back what the
// component drew. `lineSeriesAdded` counts the overlay churn under test: the
// candlestick series is created once at init and is not a line series.
import { chartDouble } from "@/test/lightweight-charts-double";
import { ExecutorChart } from "./ExecutorChart";

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { getCandles: vi.fn(async () => []) },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/**
 * A *closed* position executor. Closed matters: an open one dates its segment's
 * end at `Date.now()`, so its drawing genuinely changes between renders and no
 * key can hold it still.
 */
function closedExecutor(overrides: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "exec-1",
    type: "position",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "TERMINATED",
    close_type: "TAKE_PROFIT",
    pnl: 12.5,
    volume: 1000,
    timestamp: 1_756_000_000,
    controller_id: "ctrl-1",
    cum_fees_quote: 0.4,
    net_pnl_pct: 0.012,
    entry_price: 180,
    current_price: 184,
    close_timestamp: 1_756_003_600,
    custom_info: { close_price: 184 },
    config: {},
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

async function render(executors: ExecutorInfo[]) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ExecutorChart
          server="local"
          executors={executors}
          connector="binance"
          tradingPair="SOL-USDC"
        />
      </QueryClientProvider>,
    );
  });
  // The chart module is imported dynamically; let that promise land.
  await act(async () => {
    await Promise.resolve();
  });
}

describe("ExecutorChart overlay series", () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    chartDouble.reset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("does not rebuild the series when the prop is a new array of the same content", async () => {
    const executor = closedExecutor();
    await render([executor]);

    const drawn = chartDouble.lineSeriesAdded;
    expect(drawn).toBeGreaterThan(0);

    // What the detail panel does on every mousemove of a divider drag, and what
    // `useAgentExecutors` does on every WS frame: same executor, new array —
    // and here even a new object, the harsher case.
    await render([{ ...executor }]);
    await render([{ ...executor }]);

    expect(chartDouble.lineSeriesAdded).toBe(drawn);
    expect(chartDouble.seriesRemoved).toBe(0);
  });

  it("redraws when the segment the executor draws actually moves", async () => {
    const executor = closedExecutor();
    await render([executor]);

    const drawn = chartDouble.lineSeriesAdded;
    await render([{ ...executor, custom_info: { close_price: 191 } }]);

    expect(chartDouble.lineSeriesAdded).toBe(drawn * 2);
    expect(chartDouble.seriesRemoved).toBe(drawn);
  });

  it("redraws when an executor joins the group", async () => {
    const executor = closedExecutor();
    await render([executor]);

    const drawn = chartDouble.lineSeriesAdded;
    await render([executor, closedExecutor({ id: "exec-2", entry_price: 170 })]);

    expect(chartDouble.lineSeriesAdded).toBeGreaterThan(drawn);
    expect(chartDouble.seriesRemoved).toBe(drawn);
  });
});

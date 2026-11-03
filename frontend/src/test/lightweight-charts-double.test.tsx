/**
 * No test can reach the real `lightweight-charts` (CORR-368).
 *
 * This file is the guard on the `test.alias` in `vite.config.ts`. Delete that
 * entry and both tests below go red immediately, in the open — rather than the
 * suite starting to fail intermittently, after teardown, in whichever chart
 * test happens to mount twice in one tick.
 *
 * The second test is the exact probe that reproduced the bug: before the alias
 * it recorded 1 stub `createChart` call for 2 mounts and 7 real `<canvas>`
 * elements, because the second, concurrent `import("lightweight-charts")` was
 * answered by the real library.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { chartDouble, createChart } from "@/test/lightweight-charts-double";

// Deliberately NOT mocked here: the point is that the resolver already did it.
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

let containers: HTMLDivElement[];
let roots: Root[];
let queryClient: QueryClient;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  chartDouble.reset();
  containers = [0, 1].map(() => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  });
  roots = containers.map((el) => createRoot(el));
});

afterEach(() => {
  act(() => roots.forEach((root) => root.unmount()));
  containers.forEach((el) => el.remove());
});

describe("the lightweight-charts double", () => {
  it("is what the specifier resolves to, with no vi.mock in this file", async () => {
    const resolved = await import("lightweight-charts");

    expect(resolved.createChart as unknown).toBe(createChart);
  });

  it("answers both of two chart mounts made in one act()", async () => {
    await act(async () => {
      for (const root of roots) {
        root.render(
          <QueryClientProvider client={queryClient}>
            <ExecutorChart
              server="local"
              executors={[]}
              connector="binance"
              tradingPair="SOL-USDC"
            />
          </QueryClientProvider>,
        );
      }
    });
    // The chart module is imported dynamically; let both promises land.
    await act(async () => {
      await Promise.resolve();
    });

    expect(createChart).toHaveBeenCalledTimes(2);
    expect(chartDouble.charts).toHaveLength(2);
    // A real chart widget paints into canvases the double never creates.
    expect(containers.reduce((n, el) => n + el.querySelectorAll("canvas").length, 0)).toBe(0);
  });
});

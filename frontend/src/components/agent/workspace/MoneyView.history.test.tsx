/**
 * The Money band never walks the fleet's performance history (PERF-373).
 *
 * `MoneyView.test.tsx` mocks `useFleetData` away to pin what the view prints;
 * this renders the view over the *real* hook, so what is pinned is the network
 * the band costs. Opened alone from the home overview's money column, with no
 * Fleet disclosure beside it to share the walk, one paged history request per
 * controller on the server would buy two numbers that never read it.
 *
 * The control renders the same hook with the walk left on, over the same fakes,
 * so the absence asserted above it is the flag's doing and not the harness's.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFleetData } from "@/hooks/useFleetData";
import type { StrategySummary } from "@/lib/api";
import { MoneyView } from "./MoneyView";

const getControllerPerformanceHistoryByController = vi.fn(async () => ({
  snapshots: [],
  truncated: false,
}));

vi.mock("@/lib/api", () => ({
  api: {
    getBots: vi.fn(async () => ({
      bots: [{ bot_name: "brigado-brl_mm-btc", deployed_at: "2026-09-04T08:00:00Z" }],
      controllers: [
        {
          controller_id: "c1",
          bot_name: "brigado-brl_mm-btc",
          trading_pair: "BTC-USDT",
          global_pnl_quote: 1,
        },
      ],
    })),
    getFleetMap: vi.fn(async () => ({ owners: [], deeds: null })),
    getExecutorsPage: vi.fn(async () => ({ executors: [], next_cursor: null })),
    getStrategyPerformance: vi.fn(() => new Promise(() => {})),
    getControllerPerformanceHistoryByController: (...args: unknown[]) =>
      getControllerPerformanceHistoryByController(...(args as [])),
  },
}));

vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));

vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "brigado" }) }));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    convert: (value: number) => ({ value, converted: true }),
    formatPnlValue: String,
    formatValue: String,
    formatValueDetailed: String,
    resolvedSymbol: "$",
  }),
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

/** Whether the probe has seen the controller roster arrive. */
const rosterIn = () =>
  Number(container.querySelector("[data-roster]")?.getAttribute("data-roster") ?? 0) > 0;

const STRATEGIES = [{ slug: "brl_mm", name: "BRL MM" } as StrategySummary];

/** The hook with its default walk, as a charting host mounts it. */
function ChartingHost() {
  const fleet = useFleetData("brigado", { population: "running" });
  return <i data-roster={fleet.controllers.length} />;
}

/** The Money band, plus a probe that reports when the roster has arrived. */
function MoneyHost() {
  const fleet = useFleetData("brigado", { population: "running", history: false });
  return (
    <>
      <i data-roster={fleet.controllers.length} />
      <MoneyView
        slug="brigado"
        sslug="brl_mm"
        strategy={null}
        strategies={STRATEGIES}
        serverName="brigado"
      />
    </>
  );
}

async function render(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // Flush until the controller roster is in, since that is what arms the walk,
  // and then a few passes more for a history query to have been issued.
  for (let i = 0; i < 20 && !rosterIn(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getControllerPerformanceHistoryByController.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the Money band's network", () => {
  it("never requests the controller performance history once the roster is in", async () => {
    await render(<MoneyHost />);
    expect(rosterIn()).toBe(true);
    expect(getControllerPerformanceHistoryByController).not.toHaveBeenCalled();
  });

  it("control: the same fakes with the walk on do request it", async () => {
    await render(<ChartingHost />);
    expect(rosterIn()).toBe(true);
    expect(getControllerPerformanceHistoryByController).toHaveBeenCalled();
  });
});

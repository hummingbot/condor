/**
 * The PnL equity curve is redrawn only when the rollup changes (PERF-385).
 *
 * `PerformancePanel`'s only host, `StrategyWorkbench`, re-renders on every
 * `executors:<server>` WS frame (~2 s) while a loop runs. The panel used to
 * filter its sessions in the render body, so the `pnlData` memo never hit and
 * `AgentPnlChart` replaced the whole series (`setData`) on every one of those
 * renders. These tests count `setData`: parent re-renders and a refetch that
 * returns the same payload must cost nothing, a changed payload must redraw.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentPerformance, AgentPerformanceResponse } from "@/lib/api";
// The library is substituted at the resolver for every test file (CORR-368);
// `chartDouble` is only how this file reads back what the chart drew.
import { chartDouble } from "@/test/lightweight-charts-double";
import { PerformancePanel } from "./PerformancePanel";

const getStrategyPerformance = vi.fn<(slug: string, sslug: string) => Promise<AgentPerformanceResponse>>();

vi.mock("@/lib/api", () => ({
  api: { getStrategyPerformance: (slug: string, sslug: string) => getStrategyPerformance(slug, sslug) },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function session(overrides: Partial<AgentPerformance>): AgentPerformance {
  return {
    agent_id: "agent-1",
    session_num: 1,
    kind: "session",
    status: "stopped",
    started_at: 1_756_000_000,
    realized_pnl: 0,
    unrealized_pnl: 0,
    total_pnl: 0,
    volume: 0,
    fees: 0,
    trade_count: 0,
    win_rate: null,
    open_count: 0,
    closed_count: 0,
    executors: [],
    ...overrides,
  };
}

/** Two sessions with distinct starts: the curve only mounts past one point. */
function payload(secondPnl: number): AgentPerformanceResponse {
  return {
    slug: "brigado",
    sessions: [
      session({ session_num: 1, started_at: 1_756_000_000, total_pnl: 10, trade_count: 4, win_rate: 0.5, closed_count: 4 }),
      session({ session_num: 2, started_at: 1_756_100_000, total_pnl: secondPnl, trade_count: 6, win_rate: 1, closed_count: 6 }),
      // An experiment books nothing and is never plotted or counted.
      session({ session_num: 1, kind: "experiment", execution_mode: "dry_run", started_at: 0, total_pnl: 999 }),
    ],
    totals: { total_pnl: 10 + secondPnl, trades: 10 },
  };
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

/** The same tree each time, re-rendered from above with the panel's props unchanged. */
function renderHost(n: number) {
  return act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <div data-renders={n}>
          <PerformancePanel slug="brigado" sslug="fleet_op" />
        </div>
      </QueryClientProvider>,
    );
  });
}

const setDataCalls = () => (chartDouble.series?.setData as ReturnType<typeof vi.fn> | undefined)?.mock.calls.length ?? 0;

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  chartDouble.reset();
  getStrategyPerformance.mockReset();
  getStrategyPerformance.mockResolvedValue(payload(20));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

async function mount() {
  await renderHost(0);
  await vi.waitFor(() => expect(setDataCalls()).toBe(1));
  await settle();
}

describe("PerformancePanel equity curve (PERF-385)", () => {
  it("does not replace the series when the host re-renders with unchanged data", async () => {
    await mount();
    expect(setDataCalls()).toBe(1);

    for (let n = 1; n <= 3; n++) await renderHost(n);
    await settle();

    expect(container.querySelector("[data-renders]")?.getAttribute("data-renders")).toBe("3");
    expect(chartDouble.charts).toHaveLength(1);
    expect(setDataCalls()).toBe(1);
  });

  it("does not replace the series when the panel itself re-renders with unchanged data", async () => {
    // A prop change gets past `memo`, so this pins the sessions memo itself.
    const render = (dense: boolean) =>
      act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <PerformancePanel slug="brigado" sslug="fleet_op" dense={dense} />
          </QueryClientProvider>,
        );
      });
    await render(false);
    await vi.waitFor(() => expect(setDataCalls()).toBe(1));
    await settle();

    for (const dense of [true, false, true]) await render(dense);
    await settle();

    expect(chartDouble.charts).toHaveLength(1);
    expect(setDataCalls()).toBe(1);
  });

  it("does not replace the series when a refetch returns the same payload", async () => {
    await mount();

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["strategy-performance", "brigado", "fleet_op"] });
    });
    await settle();

    expect(getStrategyPerformance).toHaveBeenCalledTimes(2);
    expect(setDataCalls()).toBe(1);
  });

  it("redraws when a refetch brings different sessions", async () => {
    await mount();
    getStrategyPerformance.mockResolvedValue(payload(-5));

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["strategy-performance", "brigado", "fleet_op"] });
    });
    await vi.waitFor(() => expect(setDataCalls()).toBe(2));

    const setData = chartDouble.series?.setData as ReturnType<typeof vi.fn>;
    expect(setData.mock.calls[1][0]).toEqual([
      { time: 1_756_000_000, value: 10 },
      { time: 1_756_100_000, value: 5 },
    ]);
  });

  it("still renders the KPI strip from the same payload", async () => {
    await mount();
    const text = container.textContent ?? "";
    const tile = (label: string) =>
      [...container.querySelectorAll("span")].find((s) => s.textContent === label)?.nextElementSibling?.textContent;

    expect(text).toContain("Total PnL");
    // Sessions only: 4 + 6 trades, (2 + 6) wins of 10 closes; the experiment is ignored.
    expect(tile("Trades")).toBe("10");
    expect(tile("Win Rate")).toBe("80%");
    expect(tile("Total PnL")).toMatch(/30/);
  });
});

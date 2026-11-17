/**
 * A run whose history could not be read says so (FP-10).
 *
 * The run-history query's error was never read: a rejected request fell
 * through to "No performance history available", the same sentence a run with
 * nothing recorded gets. An outage must not read as an empty run, and the
 * reader needs a way to ask again — the query never retries on its own.
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

import type { BotRunInfo, ControllerInfo } from "@/lib/api";

const getRunHistory = vi.fn();

vi.mock("@/lib/api", () => ({
  api: new Proxy(
    {},
    {
      get: (_target, name) =>
        name === "getRunHistory"
          ? (...a: unknown[]) => getRunHistory(...a)
          : () => Promise.resolve({}),
    },
  ),
}));

// The pixels are the chart's own business; what it is *told* is this file's.
vi.mock("@/components/bots/PnlEvolutionChart", () => ({
  PnlEvolutionChart: ({ notice }: { notice?: { label: string } }) => (
    <div data-chart data-notice={notice?.label ?? ""} />
  ),
}));
vi.mock("@/components/bots/ControllerPnlChart", () => ({ ControllerPnlChart: () => null }));
vi.mock("@/components/charts/ExecutorChart", () => ({ ExecutorChart: () => null }));
vi.mock("@/components/editor/EditorModal", () => ({ EditorModal: () => null }));
vi.mock("@/components/bots/LogsSection", () => ({ LogsSection: () => null }));
vi.mock("@/components/bots/DeployBotDialog", () => ({ DeployBotDialog: () => null }));
vi.mock("@/components/bots/ArchivedBotDetail", () => ({ ArchivedBotDetail: () => null }));
vi.mock("@/components/perf/YamlConfigEditor", () => ({ YamlConfigEditor: () => null }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));

const { PerfBrowser } = await import("@/components/perf/PerfBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();

const RUN = {
  bot_name: "mm-sol-1",
  created_at: HOUR_AGO,
  stopped_at: new Date().toISOString(),
  is_live: false,
  controller_ids: ["grid-alpha"],
} as unknown as BotRunInfo;

const STOPPED = {
  controller_name: "grid_strike",
  controller_type: "",
  controller_id: "grid-alpha",
  bot_name: "mm-sol-1",
  status: "stopped",
  connector: "binance",
  trading_pair: "SOL-USDC",
  realized_pnl_quote: 12,
  unrealized_pnl_quote: 0,
  global_pnl_quote: 12,
  global_pnl_pct: 0.4,
  volume_traded: 5000,
  close_type_counts: {},
  positions_summary: [],
  deployed_at: HOUR_AGO,
  config: {},
} as unknown as ControllerInfo;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  getRunHistory.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(stopped: ControllerInfo, run: BotRunInfo = RUN) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/bots?population=terminated"]}>
          <PerfBrowser
            controllers={[]}
            bots={[]}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            snapshots={[]}
            runs={[run]}
            terminatedControllers={[stopped]}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const errorState = () => container.querySelector<HTMLElement>('[data-chart-state="error"]');
const chartNoticeLabel = () => container.querySelector<HTMLElement>("[data-chart]")?.dataset.notice;

describe("PerfBrowser, a terminated run's history", () => {
  it("says the read failed and Retry asks again, when there is nothing else to draw", async () => {
    getRunHistory.mockRejectedValue(new Error("upstream down"));
    // A run with no recorded end has no close to draw a fallback curve from,
    // so the box is the message.
    await render(STOPPED, { ...RUN, stopped_at: null } as unknown as BotRunInfo);

    expect(getRunHistory).toHaveBeenCalled();
    expect(errorState()?.textContent).toContain("Could not read this run's performance history");
    expect(container.textContent).not.toContain("No performance history available");

    const before = getRunHistory.mock.calls.length;
    await act(async () => {
      errorState()!.querySelector("button")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(getRunHistory.mock.calls.length).toBeGreaterThan(before);
  });

  it("captions the fallback curve as a failed read, not as a run with no history", async () => {
    getRunHistory.mockRejectedValue(new Error("upstream down"));
    await render(STOPPED);

    expect(chartNoticeLabel()).toBe("history unavailable");
  });

  it("does not claim a failure when the run simply recorded none", async () => {
    getRunHistory.mockResolvedValue({ points: 0, source: "history", controllers: [], timestamps: [] });
    await render(STOPPED);

    expect(errorState()).toBeNull();
    expect(chartNoticeLabel()).not.toBe("history unavailable");
  });
});

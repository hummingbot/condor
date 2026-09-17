/**
 * The workbench asks for the newest session's executors only while an engine
 * runs (PERF-383).
 *
 * That query's one reader is the `controllerIds` memo, which feeds
 * `useAgentExecutors` only under `hasRunning`, and everything it drives renders
 * only under `hasRunning`. Its route is uncached — a Hummingbot executor walk,
 * bot histories and the actions log on every call — yet an idle strategy left
 * open in the chat's pane asked for it every 10 s for nothing.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

vi.mock("@/lib/api", () => ({
  api: {
    getStrategy: vi.fn(),
    getAgentRuns: vi.fn(async () => []),
    getStrategySessionExecutors: vi.fn(async () => ({ performance: null })),
    getRoutineInstances: vi.fn(async () => []),
    getServers: vi.fn(async () => []),
    getStrategyPerformance: vi.fn(async () => null),
    setRestartOnBoot: vi.fn(),
  },
}));

const liveExecutors = vi.hoisted(() => ({ list: [] as unknown[] }));
vi.mock("@/hooks/useAgentExecutors", () => ({
  useAgentExecutors: (server: string | null) => ({
    executors: server ? liveExecutors.list : [],
  }),
}));

vi.mock("@/components/agent/AgentMarketStrip", () => ({
  AgentMarketStrip: () => <div data-market-strip />,
}));
vi.mock("@/components/charts/ExecutorChart", () => ({
  ExecutorChart: () => <div data-executor-chart />,
}));

vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: () => ({
    controllers: [],
    owners: [],
    bots: [],
    executors: [],
    paging: {},
    snapshots: [],
    truncated: false,
    runs: [],
    terminatedControllers: [],
    deeds: null,
    convert: (v: number) => ({ value: v, converted: true }),
    currencySymbol: "$",
    rateFormatPnl: String,
    rateFormatValue: String,
    rateFormatDetailed: String,
    isLoading: false,
    error: null,
    serverOnline: true,
  }),
}));

const { StrategyWorkbench } = await import("./StrategyWorkbench");
const { api } = await import("@/lib/api");

const base = {
  slug: "fleet_op",
  name: "PMM King BTC-BRL Fleet Operator",
  description: "Hourly autonomous operator.",
  agent_id: "brigado.fleet_op_3",
  config: { server_name: "brigado_2", frequency_sec: 3600 },
  default_trading_context: "",
  sessions: [{ number: 1 }, { number: 3 }],
  experiments: [],
  strategy_md: "",
  learnings: "",
};
const idle = { ...base, status: "stopped", instances: [] };
const running = {
  ...base,
  status: "running",
  instances: [
    {
      agent_id: "brigado.fleet_op_3",
      status: "running",
      tick_count: 1,
      daily_pnl: 0,
      risk_limits: {},
      last_tick_at: 0,
      frequency_sec: 3600,
    },
  ],
};

const getStrategy = vi.mocked(api.getStrategy);
const getExecutors = vi.mocked(api.getStrategySessionExecutors);

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;

async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <StrategyWorkbench slug="brigado" sslug="fleet_op" dense onDeleted={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await elapse(0);
  await elapse(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getStrategy.mockReset();
  getExecutors.mockClear();
  liveExecutors.list = [];
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe("the newest session's executors poll (PERF-383)", () => {
  it("never asks for an idle strategy's executors", async () => {
    getStrategy.mockResolvedValue(idle as never);
    await mount();
    // Guard: the strategy did load, with a session the query would key on.
    expect(host.textContent).toContain("2 sessions");

    await elapse(30_000);
    expect(getExecutors).toHaveBeenCalledTimes(0);
  });

  it("polls every 10s while an instance runs", async () => {
    getStrategy.mockResolvedValue(running as never);
    await mount();
    expect(getExecutors).toHaveBeenCalledTimes(1);
    expect(getExecutors).toHaveBeenLastCalledWith("brigado", "fleet_op", 3);

    await elapse(10_000);
    expect(getExecutors).toHaveBeenCalledTimes(2);
    await elapse(10_000);
    expect(getExecutors).toHaveBeenCalledTimes(3);
    expect(getExecutors).toHaveBeenLastCalledWith("brigado", "fleet_op", 3);
  });

  it("starts polling without a remount once the strategy starts", async () => {
    getStrategy.mockResolvedValue(idle as never);
    await mount();
    expect(getExecutors).toHaveBeenCalledTimes(0);

    getStrategy.mockResolvedValue(running as never);
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["strategy", "brigado", "fleet_op"] });
    });
    await elapse(0);
    expect(getExecutors).toHaveBeenCalledTimes(1);

    await elapse(10_000);
    expect(getExecutors).toHaveBeenCalledTimes(2);
  });

  it("still renders the live strip and charts for a running strategy", async () => {
    liveExecutors.list = [
      { id: "e1", connector: "binance", trading_pair: "BTC-BRL", status: "RUNNING" },
    ];
    getStrategy.mockResolvedValue(running as never);
    await mount();
    expect(host.querySelector("[data-market-strip]")).not.toBeNull();
    expect(host.querySelector("[data-executor-chart]")).not.toBeNull();
  });
});

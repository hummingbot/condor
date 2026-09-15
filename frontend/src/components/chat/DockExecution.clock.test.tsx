/**
 * The panel has two clocks, and only one of them rebuilds the fleet (PERF-342).
 *
 * `AgentRow`'s "next in 38s" has to move every second to be worth printing, so
 * the panel keeps a per-second clock for it. The *fold* — `executionRows`,
 * which builds the whole tree over the server's controllers plus every executor
 * the walk reached and calls `foldLeaves` on every node — reaches the screen
 * only through `formatRuntimeHours`, which prints whole minutes. Feeding it the
 * per-second clock re-derived a byte-identical fold sixty times a minute.
 *
 * This file counts, rather than asserts. A regression here is not a wrong
 * number on screen — every reading stays identical — it is the same work done
 * five times more often, and the only thing that can catch it is a tally of how
 * many times the builder ran per simulated minute.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControllerInfo, ExecutorInfo } from "@/lib/api";

const getBots = vi.fn();
const getExecutors = vi.fn();
const getFleetMap = vi.fn();
const getAgents = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getBots: (...a: unknown[]) => getBots(...a),
    getExecutorsPage: async (...a: unknown[]) => ({
      executors: await getExecutors(...a),
      next_cursor: null,
    }),
    getFleetMap: () => getFleetMap(),
    getAgents: () => getAgents(),
    stopControllers: () => Promise.resolve({}),
    startControllers: () => Promise.resolve({}),
    getRates: () => Promise.resolve({ rates: {} }),
  },
}));

vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => ({}) }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => vi.fn() };
});

/**
 * The tally. The real builder still runs — this counts it rather than replacing
 * it, so the rows under test are the rows the panel actually draws.
 */
const builds = { n: 0 };

vi.mock("@/components/chat/executionTree", async () => {
  const actual =
    await vi.importActual<typeof import("./executionTree")>("./executionTree");
  return {
    ...actual,
    executionRows: (input: Parameters<typeof actual.executionRows>[0]) => {
      builds.n += 1;
      return actual.executionRows(input);
    },
  };
});

const { DockExecution } = await import("./DockExecution");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "brigado_2";
/** Fixed, and on a minute boundary so a step count is a step count. */
const NOW = 1_756_000_020_000;

const CONTROLLERS: ControllerInfo[] = [
  {
    controller_name: "pmm_dynamic",
    controller_type: "",
    controller_id: "pmm_v2",
    bot_name: "brigado-brl_mm-1",
    status: "running",
    connector: "backpack",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 100,
    global_pnl_pct: 1.2,
    volume_traded: 1000,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-01T10:00:00Z",
    config: {},
  },
];

const EXECUTORS: ExecutorInfo[] = [
  {
    id: "e1",
    type: "position_executor",
    connector: "backpack",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 1,
    volume: 10,
    timestamp: 1_756_000_000,
    controller_id: "pmm_v2",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 1,
    current_price: 1,
    close_timestamp: 0,
    custom_info: {},
    config: {},
  },
];

/** One agent, looping — which is what keeps the per-second clock alive at all. */
const AGENTS = [
  {
    slug: "brigado",
    name: "Brigado",
    status: "running",
    session_count: 2,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    strategies: [
      {
        slug: "brl_mm",
        name: "BRL MM",
        session_count: 2,
        server_name: SERVER,
        instances: [
          {
            agent_id: "brigado.brl_mm_2",
            status: "running",
            tick_count: 412,
            // Far enough out that the countdown never crosses zero and changes
            // which branch of the label runs during the window measured.
            last_tick_at: NOW / 1000 + 600,
            frequency_sec: 3600,
            last_action: "",
            last_did: { tick: 12, ok: true, summary: "deployed grid_strike" },
            server_name: SERVER,
          },
        ],
      },
    ],
  },
];

const FLEET_MAP = {
  owners: [
    {
      runKey: "brigado.brl_mm",
      agentSlug: "brigado",
      agentName: "Brigado",
      strategySlug: "brl_mm",
      strategyName: "brl_mm",
      namespace: "brigado-brl_mm",
      declaredBots: [],
      agentIds: [],
      live: null,
    },
  ],
  deeds: { bots: {}, since: 0 },
};

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

async function render() {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <DockExecution server={SERVER} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

/** Walk the wall clock forward one second at a time, as a real minute does. */
async function tickSeconds(count: number) {
  for (let i = 0; i < count; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  builds.n = 0;
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The same objects on every poll, so react-query's structural sharing keeps
  // the identities stable and the only thing that can invalidate the memo is
  // the clock — which is the whole subject of this file.
  getBots.mockResolvedValue({
    controllers: CONTROLLERS,
    bots: [],
    total_pnl: 0,
    total_volume: 0,
  });
  getExecutors.mockResolvedValue(EXECUTORS);
  getFleetMap.mockResolvedValue(FLEET_MAP);
  getAgents.mockResolvedValue(AGENTS);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("the fold's clock", () => {
  it("does not rebuild the fleet tree once a second", async () => {
    await render();

    // The countdown proves the per-second clock is running at all: without a
    // live loop this test would pass by measuring nothing.
    expect(container.querySelector("[data-agent-due]")).not.toBeNull();

    const before = builds.n;
    await tickSeconds(50);
    const perMinute = builds.n - before;

    // Fifty seconds inside one minute-quantised tick: the clock's snapshot does
    // not change, so nothing here may rebuild the tree. On the per-second clock
    // this was fifty rebuilds.
    expect(perMinute).toBe(0);
  });

  it("still re-folds when the minute turns, so runtimes advance", async () => {
    await render();

    const before = builds.n;
    // Across the boundary — 50s in was still the same minute, 70s is the next.
    await tickSeconds(70);

    expect(builds.n - before).toBe(1);
  });

  it("keeps counting the next tick down every second", async () => {
    await render();

    const due = () =>
      container.querySelector("[data-agent-due]")?.textContent ?? "";
    const first = due();
    await tickSeconds(3);

    // The label is seconds-resolution and must have moved, which is the thing
    // the coarse clock is deliberately not allowed to take away.
    expect(first).not.toBe("");
    expect(due()).not.toBe(first);
  });
});

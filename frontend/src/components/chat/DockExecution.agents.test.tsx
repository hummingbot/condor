/**
 * The Execution panel is the fleet now (FEAT-114).
 *
 * `/fleet` answered *"what is every agent doing"* on a page of its own and is
 * gone; its rows are the top level of this panel. What that has to keep true is
 * everything the page carried and the old panel could not say — a live dot, the
 * last decision, the next tick — plus the two properties that make it a *fold*
 * rather than a second opinion: the agent rows partition the controllers, and
 * nothing in the payload is dropped for having no owner.
 *
 * And the click. An agent row opens **that** agent's panel, not the
 * conversation's, which is what `?panel=agent&who=` exists for.
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

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigate };
});

const { DockExecution } = await import("./DockExecution");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "brigado_2";
/** Fixed, so a countdown is a number this file can name rather than a race. */
const NOW = 1_756_000_000_000;

function controller(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
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
    ...over,
  };
}

function executor(over: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
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
    ...over,
  };
}

function owner(slug: string, sslug: string) {
  return {
    runKey: `${slug}.${sslug}`,
    agentSlug: slug,
    agentName: slug === "brigado" ? "Brigado" : "Quiet",
    strategySlug: sslug,
    strategyName: sslug,
    namespace: `${slug}-${sslug}`,
    declaredBots: [],
    agentIds: [],
    live: null,
  };
}

/** One agent with a loop that ticked 40s ago on a 60s cadence. */
function agent(slug: string, name: string, over: Record<string, unknown> = {}) {
  return {
    slug,
    name,
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
            agent_id: `${slug}.brl_mm_2`,
            status: "running",
            tick_count: 412,
            last_tick_at: NOW / 1000 - 20,
            frequency_sec: 60,
            last_action: "",
            last_did: {
              tick: 12,
              ok: true,
              summary: "deployed grid_strike on SOL-USDC",
            },
            server_name: SERVER,
          },
        ],
      },
    ],
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;
const onOpenAgent = vi.fn();

async function render(props: { onOpenAgent?: (slug: string) => void } = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <DockExecution server={SERVER} {...props} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

const agentRows = () => [...container.querySelectorAll("[data-agent-row]")];
const agentRow = (id: string) =>
  container.querySelector<HTMLElement>(`[data-agent-row="agent:${id}"]`)!;
const controllerRows = () => [...container.querySelectorAll("[data-controller-row]")];
const counts = () =>
  container.querySelector('[data-testid="execution-counts"]')!.textContent ?? "";
const text = () => container.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.setSystemTime(NOW);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  getBots.mockResolvedValue({
    controllers: [
      controller(),
      controller({ controller_id: "grid_v1", bot_name: "quiet-brl_mm-1", global_pnl_quote: 25 }),
    ],
    bots: [],
    total_pnl: 0,
    total_volume: 0,
  });
  getExecutors.mockResolvedValue([executor()]);
  getFleetMap.mockResolvedValue({
    owners: [owner("brigado", "brl_mm"), owner("quiet", "brl_mm")],
    deeds: { bots: {}, since: 0 },
  });
  getAgents.mockResolvedValue([agent("brigado", "Brigado"), agent("quiet", "Quiet")]);
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

describe("the agent rows", () => {
  it("gives one row per agent, and they add up to the fleet", async () => {
    await render();

    expect(agentRows()).toHaveLength(2);
    expect(text()).toContain("Brigado / brl_mm");
    expect(text()).toContain("Quiet / brl_mm");
    // The fold, not the run rollup: `/bots` prints these same two numbers for
    // these same scopes, and 100 + 25 is the panel's whole fleet.
    expect(agentRow("brigado.brl_mm").textContent).toContain("+$100.00");
    expect(agentRow("quiet.brl_mm").textContent).toContain("+$25.00");
  });

  it("expands into the controllers that agent deployed", async () => {
    await render();

    // Two agents is a small fleet, so both arrive open.
    expect(controllerRows()).toHaveLength(2);

    const twisty = agentRow("brigado.brl_mm").querySelector(
      "[data-execution-twisty]",
    )!;
    expect(twisty.getAttribute("aria-expanded")).toBe("true");
    await click(twisty);
    // Shut, its controller goes with it and the other agent's stays.
    expect(controllerRows()).toHaveLength(1);
    expect(agentRows()).toHaveLength(2);
    expect(text()).toContain("grid_v1");
    expect(text()).not.toContain("pmm_v2");
  });

  it("shows a live dot, the last decision and a next-tick countdown", async () => {
    await render();

    const row = agentRow("brigado.brl_mm");
    expect(row.querySelector("[data-agent-live]")).not.toBeNull();
    expect(row.querySelector("[data-agent-decision]")!.textContent).toBe(
      "deployed grid_strike on SOL-USDC",
    );
    // 60s cadence, ticked 20s ago.
    expect(row.querySelector("[data-agent-due]")!.textContent).toContain("next in");
  });

  it("keeps a stopped agent listed, with neither dot nor countdown", async () => {
    getAgents.mockResolvedValue([
      agent("brigado", "Brigado", {
        status: "idle",
        strategies: [
          { slug: "brl_mm", name: "BRL MM", session_count: 2, server_name: SERVER, instances: [] },
        ],
      }),
      agent("quiet", "Quiet"),
    ]);

    await render();

    const row = agentRow("brigado.brl_mm");
    // Still there — its records are on screen, so hiding the agent behind them
    // would leave the money with no owner.
    expect(row).not.toBeNull();
    expect(row.querySelector("[data-agent-live]")).toBeNull();
    expect(row.querySelector("[data-agent-due]")).toBeNull();
  });

  it("counts what is looping beside what is deployed", async () => {
    await render();

    expect(counts()).toContain("2 looping");
    expect(counts()).toContain("2 controllers");
    expect(counts()).toContain("1 executor");
  });

  it("opens that agent's panel, not the conversation's", async () => {
    await render({ onOpenAgent });

    await click(agentRow("quiet.brl_mm").querySelector("[data-agent-open]")!);
    expect(onOpenAgent).toHaveBeenCalledWith("quiet");
    // The pane took it: nothing navigated away from the conversation.
    expect(navigate).not.toHaveBeenCalled();
  });

  it("falls back to the agent's own page when there is no pane to open", async () => {
    await render();

    await click(agentRow("quiet.brl_mm").querySelector("[data-agent-open]")!);
    expect(navigate).toHaveBeenCalledWith("/agents/quiet");
  });
});

describe("what no agent owns", () => {
  it("is a named bucket, not a dropped row", async () => {
    getBots.mockResolvedValue({
      controllers: [controller(), controller({ bot_name: "handrolled-1", controller_id: "hand_v1" })],
      bots: [],
      total_pnl: 0,
      total_volume: 0,
    });

    await render();

    // `since: 0` — this install's log has never been complete, so a record it
    // cannot credit is unjudgeable rather than an accusation.
    expect(text()).toContain("Before the ledger");
    expect(controllerRows()).toHaveLength(2);
    expect(counts()).toContain("2 controllers");
  });

  it("names an agent trading on another server instead of showing it a zero", async () => {
    getAgents.mockResolvedValue([
      agent("brigado", "Brigado"),
      agent("elsewhere", "Elsewhere", {
        strategies: [
          {
            slug: "brl_mm",
            name: "BRL MM",
            session_count: 1,
            server_name: "other_box",
            instances: [],
          },
        ],
      }),
    ]);

    await render();

    const note = container.querySelector("[data-execution-elsewhere]")!;
    expect(note.textContent).toContain("Elsewhere");
    expect(note.textContent).toContain("trades on other_box");
    // And it is not given a row of dashes among the agents that trade here.
    expect(agentRows().some((r) => r.textContent?.includes("Elsewhere"))).toBe(false);
  });
});

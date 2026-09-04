/**
 * The overview has to beat the grid that was deleted from this address.
 *
 * `Agents.tsx`'s old docstring is the standard: a page whose only unique job is
 * showing which agents are running does not earn a place, because the rail's
 * live line already does that. So what is pinned here is the three things the
 * line cannot say — attributed money, the last decision as an address, and the
 * next tick — plus the one honesty rule that separates this from the grid's
 * numbers: a run that has claimed nothing shows a dash, not `$0.00`.
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

import type { FleetData } from "@/hooks/useFleetData";
import type {
  AgentSummary,
  ControllerInfo,
  RunningInstance,
  StrategySummary,
} from "@/lib/api";
import { FleetOverview } from "./FleetOverview";

const getAgents = vi.fn<() => Promise<AgentSummary[]>>();

vi.mock("@/lib/api", () => ({
  api: { getAgents: () => getAgents() },
}));

/**
 * The fleet a row folds, swapped per test (ARCH-324).
 *
 * A row only fetches one when its agent has a server to fold on, so every test
 * above that declares no server never reaches this at all — which is the dash
 * case, and the reason those tests need nothing from here.
 */
const fleetData = vi.fn<(server: string) => Partial<FleetData>>();
vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: (server: string) => fleetData(server),
}));

const OWNERS = [
  {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [] as string[],
    agentIds: [] as string[],
    live: null,
  },
];

function controller(over: Partial<ControllerInfo>): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "c1",
    bot_name: "brigado-brl_mm-btc",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions: [],
    deployed_at: "2026-09-04T08:00:00Z",
    ...over,
  } as unknown as ControllerInfo;
}

function fleet(controllers: ControllerInfo[]): Partial<FleetData> {
  return {
    controllers,
    executors: [],
    owners: OWNERS,
    deeds: { bots: {}, since: 1 },
    convert: (value: number) => ({ value, converted: true }),
    currencySymbol: "$",
  } as Partial<FleetData>;
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function instance(over: Partial<RunningInstance> = {}): RunningInstance {
  return {
    agent_id: "brigado.brl_mm_1",
    status: "running",
    tick_count: 42,
    last_tick_at: 1_000,
    frequency_sec: 60,
    last_action: "",
    last_did: null,
    server_name: "brigado",
    ...over,
  } as RunningInstance;
}

function strategy(over: Partial<StrategySummary> = {}): StrategySummary {
  return {
    slug: "brl_mm",
    name: "BRL MM",
    session_count: 3,
    instances: [],
    ...over,
  } as StrategySummary;
}

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    slug: "brigado",
    name: "Brigado",
    agent_key: "claude-opus",
    status: "idle",
    session_count: 3,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    strategies: [],
    instances: [],
    ...over,
  } as AgentSummary;
}

async function render(agents: AgentSummary[]) {
  getAgents.mockResolvedValue(agents);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <FleetOverview />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  // A second flush, and a real turn of the loop rather than a microtask:
  // react-query settles the fetch on the first and only paints what it
  // returned on the next tick. Without this every assertion below reads an
  // empty list and the failure looks like a render bug.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const text = () => container.textContent ?? "";
const rows = () =>
  [...container.querySelectorAll<HTMLElement>("[data-fleet-row]")];
const pick = (slug: string) =>
  container.querySelector<HTMLElement>(`[data-fleet-row="${slug}"]`)!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  fleetData.mockReturnValue(fleet([]));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  getAgents.mockReset();
  fleetData.mockReset();
  vi.useRealTimers();
});

describe("the order", () => {
  it("is running first, then by what has actually been attributed", async () => {
    await render([
      agent({
        slug: "idle_rich",
        name: "Idle Rich",
        total_pnl: 900,
        total_volume: 10,
        strategies: [strategy()],
      }),
      agent({
        slug: "looping",
        name: "Looping",
        status: "running",
        total_pnl: -20,
        total_volume: 10,
        strategies: [strategy({ instances: [instance()] })],
      }),
    ]);

    expect(rows().map((r) => r.dataset.fleetRow)).toEqual([
      "looping",
      "idle_rich",
    ]);
  });
});

describe("the money", () => {
  it("is a dash, never a zero, when its records say nothing", async () => {
    await render([agent({ server_name: "brigado", strategies: [strategy()] })]);

    const net = pick("brigado").querySelector("[data-fleet-net]")!;
    const volume = pick("brigado").querySelector("[data-fleet-volume]")!;
    expect(net.textContent).toBe("—");
    expect(net.textContent).not.toContain("0.00");
    expect(volume.textContent).toContain("—");
  });

  it("is the fold of its records when they have something to say", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ global_pnl_quote: 64.12, volume_traded: 2_549_843 }),
      ]),
    );
    await render([agent({ server_name: "brigado", strategies: [strategy()] })]);

    expect(
      pick("brigado").querySelector("[data-fleet-net]")!.textContent,
    ).toContain("+$64.12");
    expect(
      pick("brigado").querySelector("[data-fleet-volume]")!.textContent,
    ).toContain("$2.5M");
  });

  it("ignores the run rollup entirely — the column is the fold now", async () => {
    fleetData.mockReturnValue(fleet([controller({ global_pnl_quote: 91 })]));
    await render([
      agent({
        server_name: "brigado",
        total_pnl: 64,
        total_volume: 5_000,
        strategies: [strategy()],
      }),
    ]);

    const net = pick("brigado").querySelector("[data-fleet-net]")!.textContent;
    expect(net).toContain("91");
    expect(net).not.toContain("64");
  });
});

describe("an agent with no server (ARCH-324)", () => {
  it("shows a dash rather than folding whichever fleet is at hand", async () => {
    // A real fleet, with real money in it, on the server the app happens to be
    // pointed at. The agent has declared none, so none of it is its money.
    fleetData.mockReturnValue(fleet([controller({ global_pnl_quote: 91 })]));
    await render([agent({ strategies: [strategy()] })]);

    const money = pick("brigado").querySelector("[data-fleet-money]")!;
    expect(money.querySelector("[data-fleet-net]")!.textContent).toBe("—");
    expect(money.querySelector("[data-fleet-net]")!.textContent).not.toContain(
      "0.00",
    );
    expect(money.querySelector("[data-fleet-volume]")!.textContent).toContain(
      "—",
    );
    expect(
      money.querySelector("[data-fleet-money-label]")!.textContent,
    ).toContain("no server to fold");
  });

  it("says nothing while the declared server has not answered", async () => {
    // A declared server whose fleet is still empty is not `$0.00` either — it
    // is a server that has not spoken yet.
    fleetData.mockReturnValue(fleet([]));
    await render([agent({ server_name: "brigado", strategies: [strategy()] })]);

    expect(
      pick("brigado").querySelector("[data-fleet-net]")!.textContent,
    ).toBe("—");
  });
});

describe("which server a row folds on (ARCH-324)", () => {
  it("is the strategy's own, not the agent's pin, when the two differ", async () => {
    // `AgentWorkspace` opens the Money view against the strategy's configured
    // server and falls back to the agent's pin. A row that resolved it the
    // other way round would fold a fleet the Money view never looks at.
    fleetData.mockImplementation((server: string) =>
      fleet([
        controller({ global_pnl_quote: server === "brigado" ? 91 : 500 }),
      ]),
    );
    await render([
      agent({
        server_name: "the_agents_pin",
        strategies: [strategy({ server_name: "brigado" })],
      }),
    ]);

    const net = pick("brigado").querySelector("[data-fleet-net]")!.textContent;
    expect(net).toContain("91");
    expect(net).not.toContain("500");
  });
});

describe("the last decision", () => {
  it("is a link into the tick that made it", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [
              instance({
                last_did: {
                  tick: 42,
                  at: 0,
                  tool: "manage_bots",
                  verb: "manage_bots:deploy",
                  summary: "Deploy brigado-brl_mm",
                  ok: true,
                  error: "",
                },
              }),
            ],
          }),
        ],
      }),
    ]);

    const link = pick("brigado").querySelector<HTMLAnchorElement>(
      "[data-fleet-decision] a",
    )!;
    expect(link.textContent).toContain("Deploy brigado-brl_mm");
    expect(link.getAttribute("href")).toBe(
      "/agents/brigado?view=tick&strategy=brl_mm&tick=42",
    );
  });

  it("falls back to what the loop last said when it has done nothing", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [instance({ last_action: "Held the range" })],
          }),
        ],
      }),
    ]);
    expect(
      pick("brigado").querySelector("[data-fleet-decision]")!.textContent,
    ).toContain("Held the range");
  });
});

describe("the next tick", () => {
  it("counts down on every looping row", async () => {
    vi.setSystemTime(new Date(1_030_000));
    await render([
      agent({
        status: "running",
        strategies: [strategy({ instances: [instance()] })],
      }),
    ]);
    expect(text()).toContain("next in");
  });
});

describe("the alerts", () => {
  it("surface a failed deed on the home page, addressed to its tick", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [
              instance({
                last_did: {
                  tick: 9,
                  at: 0,
                  tool: "manage_controllers",
                  verb: "manage_controllers:upsert",
                  summary: "Upsert controller pmm_1",
                  ok: false,
                  error: "rejected",
                },
              }),
            ],
          }),
        ],
      }),
    ]);

    const alert = pick("brigado").querySelector<HTMLAnchorElement>(
      '[data-fleet-alert="failed"]',
    )!;
    expect(alert.textContent).toContain("Upsert controller pmm_1");
    expect(alert.getAttribute("href")).toContain("tick=9");
  });
});

describe("an agent with no strategies", () => {
  it("renders once, as a name and a never-run, and not as a row", async () => {
    await render([
      agent({ slug: "bare", name: "Bare" }),
      agent({ slug: "brigado", strategies: [strategy()] }),
    ]);

    expect(rows().map((r) => r.dataset.fleetRow)).toEqual(["brigado"]);
    const listed = container.querySelectorAll('[data-fleet-strategyless="bare"]');
    expect(listed.length).toBe(1);
    expect(listed[0].textContent).toContain("never run");
  });
});

describe("the money column says which number it is (FEAT-109 / ARCH-324)", () => {
  it("names it as what the records show and links to the reconciliation", async () => {
    fleetData.mockReturnValue(fleet([controller({ global_pnl_quote: 64 })]));
    await render([
      agent({
        slug: "brigado",
        server_name: "brigado",
        total_pnl: 64,
        total_volume: 5_000,
        strategies: [strategy()],
      }),
    ]);

    const money = pick("brigado").querySelector<HTMLAnchorElement>("[data-fleet-money]")!;
    expect(money.textContent).toContain("what its records show");
    // The `?strategy=` is what narrows the Money view's fold to the run keys
    // this row folded — the link and the number are the same scope.
    expect(money.getAttribute("href")).toBe("/agents/brigado?view=money&strategy=brl_mm");
  });

  it("keeps the dash rule: records that say nothing show no number at all", async () => {
    await render([
      agent({ slug: "brigado", server_name: "brigado", strategies: [strategy()] }),
    ]);

    const money = pick("brigado").querySelector("[data-fleet-money]")!;
    expect(money.querySelector("[data-fleet-net]")?.textContent).toBe("—");
    expect(money.textContent).toContain("what its records show");
  });
});

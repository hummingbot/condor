/**
 * The home row and the Money headline are one number (ARCH-324).
 *
 * FEAT-109's last acceptance criterion, which it could not meet: *"a row on the
 * home overview shows the same money as the Money headline"*. It could not
 * because `AgentSummary` carried no server and a fold is computed over a
 * server's records, so the home kept the run rollup and said so — two correct
 * numbers for one agent, on two screens, that read as a contradiction.
 *
 * The summary carries its server now, so this is the test that closes it, and
 * it is written the way FEAT-109's reconciliation tests are: **one fixture,
 * both screens**. Not two assertions against a literal — a literal would keep
 * passing while both screens drifted together away from the fleet, and would
 * keep passing if either stopped reading the fixture at all. The row's own link
 * decides the scope the Money view is rendered at, so what is pinned is the
 * whole claim: *the number you clicked is the number you land on*.
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
import type { AgentSummary, ControllerInfo, StrategySummary } from "@/lib/api";
import { FleetOverview } from "./FleetOverview";
import { MoneyView } from "./MoneyView";

const getAgents = vi.fn<() => Promise<AgentSummary[]>>();
const getStrategyPerformance = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgents: () => getAgents(),
    getStrategyPerformance: (slug: string, sslug: string) =>
      getStrategyPerformance(slug, sslug),
  },
}));

// The rollup band is `PerformancePanel` unchanged and is not what is being
// reconciled here; a marker keeps this file on the headline.
vi.mock("@/components/agent/AgentOverviewTab", () => ({
  PerformancePanel: () => <div data-rollup />,
}));

const fleetData = vi.fn<() => Partial<FleetData>>();
vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: () => fleetData(),
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

// ── One fixture, read by both screens ──

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
    volume_traded: 1_000,
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
    // `sol_scalper` is a bot the chat deployed: this agent's money, owned by no
    // strategy — the term that makes the fold and the rollup differ, and the
    // reason a row that quietly summed its strategies would get this wrong.
    deeds: {
      bots: { sol_scalper: { runKey: "brigado.chat", runId: "c1", at: 1 } },
      since: 1,
    },
    convert: (value: number) => ({ value, converted: true }),
    currencySymbol: "$",
  } as Partial<FleetData>;
}

const STRATEGIES = [
  { slug: "brl_mm", name: "BRL MM", server_name: "brigado" } as StrategySummary,
];

const AGENT = {
  slug: "brigado",
  name: "Brigado",
  agent_key: "",
  status: "idle",
  session_count: 3,
  // Deliberately unlike anything the fleet says: the rollup is the *other*
  // number, and a row still printing it would pass nothing below.
  total_pnl: 64,
  total_volume: 5_000,
  open_positions: 0,
  server_name: "brigado",
  strategies: STRATEGIES,
  instances: [],
} as unknown as AgentSummary;

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

/** Flush until both screens have settled — the Money view awaits its rollup. */
async function settle(passes = 10) {
  for (let i = 0; i < passes; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The home, rendered — returns what its money column says and where it links. */
async function home(agents: AgentSummary[]) {
  getAgents.mockResolvedValue(agents);
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client()}>
          <FleetOverview />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
  const money = container.querySelector<HTMLAnchorElement>("[data-fleet-money]")!;
  return {
    net: money.querySelector("[data-fleet-net]")!.textContent,
    volume: money.querySelector("[data-fleet-volume]")!.textContent,
    href: money.getAttribute("href")!,
  };
}

/** The Money view, rendered at the scope the row's own link addresses. */
async function money(href: string) {
  const url = new URL(href, "https://x");
  const strategy = url.searchParams.get("strategy");
  const slug = decodeURIComponent(url.pathname.split("/")[2]);
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client()}>
          <MoneyView
            slug={slug}
            sslug={strategy ?? "brl_mm"}
            strategy={strategy}
            strategies={STRATEGIES}
            serverName="brigado"
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
  return {
    net: container.querySelector("[data-money-net]")!.textContent,
    volume: container.querySelector("[data-money-volume]")!.textContent,
  };
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getStrategyPerformance.mockResolvedValue({
    totals: { total_pnl: 64 },
    sessions: [],
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  getAgents.mockReset();
  getStrategyPerformance.mockReset();
  fleetData.mockReset();
  vi.clearAllMocks();
});

describe("the fold on a home row and the Money headline", () => {
  it("are the same number, over one fixture, at the scope the row links to", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ controller_id: "a", global_pnl_quote: 64 }),
        controller({
          controller_id: "b",
          bot_name: "sol_scalper",
          global_pnl_quote: 27,
          volume_traded: 500,
        }),
      ]),
    );

    const row = await home([AGENT]);
    const headline = await money(row.href);

    // Both fold the strategy's records *and* what the chat deployed: 64 + 27.
    expect(headline.net).toContain("91");
    expect(row.net).toBe(headline.net);
    expect(row.volume).toContain(headline.volume!);

    // And it is not the rollup wearing the fold's label.
    expect(row.net).not.toContain("64.00");
  });

  it("are both a dash when the records say nothing at all", async () => {
    fleetData.mockReturnValue(fleet([]));
    getStrategyPerformance.mockResolvedValue({
      totals: { total_pnl: 0 },
      sessions: [],
    });

    const row = await home([{ ...AGENT, total_pnl: 0, total_volume: 0 }]);
    const headline = await money(row.href);

    expect(row.net).toBe("—");
    expect(headline.net).toBe("—");
    expect(row.net).toBe(headline.net);
  });
});

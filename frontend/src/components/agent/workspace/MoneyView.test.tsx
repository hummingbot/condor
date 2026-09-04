/**
 * The Money view says two things and never conflates them (FEAT-109).
 *
 * `reconcile.test.ts` pins the arithmetic; this pins the promise the screen
 * makes about it. Four ways the view could quietly go back to lying: printing
 * the rollup as though it were the fold, dropping the reconciliation band so
 * two numbers stand side by side unexplained, swallowing a residual instead of
 * naming it *unaccounted*, and printing `$0.00` for an agent whose records say
 * nothing at all.
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

import type { ControllerInfo, StrategySummary } from "@/lib/api";
import type { FleetData } from "@/hooks/useFleetData";
import { MoneyView } from "./MoneyView";

const getStrategyPerformance = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getStrategyPerformance: (slug: string, sslug: string) =>
      getStrategyPerformance(slug, sslug),
  },
}));

// The rollup band is `PerformancePanel` unchanged, and this file is about what
// surrounds it: a marker keeps the assertion on "the rollup is present, under
// the heading that says what it is" without dragging in an equity chart.
vi.mock("@/components/agent/AgentOverviewTab", () => ({
  PerformancePanel: ({ slug, sslug }: { slug: string; sslug: string }) => (
    <div data-rollup={`${slug}.${sslug}`} />
  ),
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

function fleet(controllers: ControllerInfo[], over: Partial<FleetData> = {}) {
  return {
    controllers,
    executors: [],
    owners: OWNERS,
    deeds: {
      bots: { sol_scalper: { runKey: "brigado.chat", runId: "c1", at: 1 } },
      since: 1,
    },
    convert: (value: number) => value,
    currencySymbol: "$",
    ...over,
  } as Partial<FleetData>;
}

const STRATEGIES = [{ slug: "brl_mm", name: "BRL MM" } as StrategySummary];

function render(strategies: readonly StrategySummary[] = STRATEGIES) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <MoneyView
            slug="brigado"
            sslug="brl_mm"
            strategy={null}
            strategies={strategies}
            serverName="brigado"
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

/** Let the rollup queries settle, so the reconciliation band has both numbers. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getStrategyPerformance.mockReset();
  fleetData.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the two numbers", () => {
  it("leads with the fold and keeps the rollup beside it, labelled", async () => {
    fleetData.mockReturnValue(
      fleet([controller({ controller_id: "a", global_pnl_quote: 64 })]),
    );
    getStrategyPerformance.mockResolvedValue({ totals: { total_pnl: 64 }, sessions: [] });

    render();
    await settle();

    expect(container.querySelector("[data-money-net]")?.textContent).toContain("64");
    // The rollup is present, under a heading that says it is about runs.
    expect(container.querySelector("[data-rollup='brigado.brl_mm']")).not.toBeNull();
    expect(container.textContent).toContain("What its runs earned");
    expect(container.textContent).toContain("What its records show");
  });

  it("names a chat-deployed bot as a term and links to its records", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ controller_id: "a", global_pnl_quote: 64 }),
        controller({ controller_id: "b", bot_name: "sol_scalper", global_pnl_quote: 27 }),
      ]),
    );
    getStrategyPerformance.mockResolvedValue({ totals: { total_pnl: 64 }, sessions: [] });

    render();
    await settle();

    expect(container.querySelector("[data-money-net]")?.textContent).toContain("91");
    const term = container.querySelector("[data-money-term='agent:brigado.chat']");
    expect(term).not.toBeNull();
    expect(term?.textContent).toContain("Deployed from chat");
    expect(term?.querySelector("a")?.getAttribute("href")).toBe(
      "/bots?scope=agent%3Abrigado.chat",
    );
    // Named, so it is not a residual.
    expect(container.querySelector("[data-money-unaccounted]")).toBeNull();
  });

  it("shows a residual as unaccounted rather than folding it into a term", async () => {
    fleetData.mockReturnValue(
      fleet([controller({ controller_id: "a", global_pnl_quote: 94 })]),
    );
    getStrategyPerformance.mockResolvedValue({ totals: { total_pnl: 74 }, sessions: [] });

    render();
    await settle();

    const residual = container.querySelector("[data-money-unaccounted]");
    expect(residual).not.toBeNull();
    expect(residual?.textContent).toContain("Unaccounted");
    expect(residual?.textContent).toContain("20");
  });

  it("holds the reconciliation back until the rollup has answered", () => {
    fleetData.mockReturnValue(
      fleet([controller({ controller_id: "a", global_pnl_quote: 64 })]),
    );
    getStrategyPerformance.mockReturnValue(new Promise(() => {}));

    render();

    const band = container.querySelector("[data-money-reconciliation]");
    expect(band?.textContent).toContain("nothing to reconcile");
    expect(container.querySelector("[data-money-unaccounted]")).toBeNull();
  });
});

describe("the dash rule", () => {
  it("prints a dash, never $0.00, for an agent whose records say nothing", async () => {
    fleetData.mockReturnValue(fleet([]));
    getStrategyPerformance.mockResolvedValue({ totals: { total_pnl: 0 }, sessions: [] });

    render();
    await settle();

    expect(container.querySelector("[data-money-net]")?.textContent).toBe("—");
    expect(container.querySelector("[data-money-volume]")?.textContent).toBe("—");
  });
});

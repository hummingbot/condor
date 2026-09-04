/**
 * The page, as the reader meets it.
 *
 * `floor.test.ts` pins the arithmetic and `owner-series.test.ts` pins the
 * chart's invariant; what is left to pin here is the part that only exists once
 * something is rendered — that the strip prints the fold, that the residual gets
 * a row *with a lead* rather than being swept into an "other", and that the four
 * things this page cannot measure are captioned as not measured instead of
 * drawn as empty panels.
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
  ControllerPerformanceSnapshot,
} from "@/lib/api";
import { ServerContext } from "@/hooks/useServer";
import { Floor } from "@/pages/Floor";

const getAgents = vi.fn<() => Promise<AgentSummary[]>>();
vi.mock("@/lib/api", () => ({
  api: { getAgents: () => getAgents() },
}));

const fleetData = vi.fn<(server: string) => Partial<FleetData>>();
vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: (server: string) => fleetData(server),
}));

const OWNERS = [
  {
    runKey: "alpha.mm",
    agentSlug: "alpha",
    agentName: "Alpha",
    strategySlug: "mm",
    strategyName: "MM",
    namespace: "alpha-mm",
    declaredBots: [] as string[],
    agentIds: [] as string[],
    live: null,
  },
  {
    runKey: "ghost.mm",
    agentSlug: "ghost",
    agentName: "Ghost",
    strategySlug: "mm",
    strategyName: "MM",
    namespace: "ghost-mm",
    declaredBots: [] as string[],
    agentIds: [] as string[],
    live: null,
  },
];

let seq = 0;
function controller(over: Partial<ControllerInfo>): ControllerInfo {
  seq += 1;
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: `c${seq}`,
    bot_name: "alpha-mm-1",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-04T08:00:00Z",
    ...over,
  } as unknown as ControllerInfo;
}

function snap(
  bot: string,
  controller: string,
  at: string,
  pnl: number,
): ControllerPerformanceSnapshot {
  return {
    timestamp: at,
    bot_name: bot,
    controller_id: controller,
    controller_name: "pmm_simple",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: pnl,
    unrealized_pnl_quote: 0,
    global_pnl_quote: pnl,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
  };
}

function fleet(
  controllers: ControllerInfo[],
  snapshots: ControllerPerformanceSnapshot[] = [],
): Partial<FleetData> {
  return {
    controllers,
    executors: [],
    snapshots,
    owners: OWNERS,
    deeds: { bots: {}, since: 1 },
    convert: (value: number) => ({ value, converted: true }),
    currencySymbol: "$",
  } as Partial<FleetData>;
}

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    slug: "alpha",
    name: "Alpha",
    agent_key: "",
    status: "idle",
    session_count: 1,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    server_name: "s1",
    strategies: [{ slug: "mm", name: "MM", session_count: 1, instances: [] }],
    instances: [],
    ...over,
  } as AgentSummary;
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function render(
  agents: AgentSummary[],
  server: string | null = "s1",
  url = "/floor",
) {
  getAgents.mockResolvedValue(agents);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <QueryClientProvider client={client}>
          <ServerContext value={{ server, setServer: () => {} }}>
            <Floor />
          </ServerContext>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  // A second flush, and a real turn of the loop: react-query settles the fetch
  // on the first and only paints what it returned on the next tick.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const text = () => container.textContent ?? "";
const pick = (selector: string) => container.querySelector<HTMLElement>(selector);

beforeEach(() => {
  seq = 0;
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
});

describe("the strip", () => {
  it("prints the fleet fold, and the rows it is the sum of", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ global_pnl_quote: 64.12, volume_traded: 2_549_843 }),
      ]),
    );
    await render([agent()]);

    expect(pick("[data-floor-net]")!.textContent).toContain("+$64.12");
    expect(pick("[data-floor-volume]")!.textContent).toContain("$2.5M");
    expect(
      pick('[data-floor-row="alpha"] [data-floor-cell="net"]')!.textContent,
    ).toContain("+$64.12");
  });

  it("suppresses the normalized readings rather than printing them as zero", async () => {
    await render([agent()]);
    // No volume and no declared capital: fees-of-volume and turnover have no
    // denominator, and `0.0 bps` is a statement about a fleet that traded.
    expect(pick("[data-floor-bps]")!.textContent).toBe("—");
    expect(pick("[data-floor-turnover]")!.textContent).toBe("—");
    expect(text()).not.toContain("0.0 bps");
  });

  it("captions the fee reading as executor-only", async () => {
    await render([agent()]);
    expect(text()).toContain("executor-only");
  });

  it("says out loud what it does not measure", async () => {
    await render([agent()]);
    const note = pick("[data-floor-not-measured]")!.textContent ?? "";
    expect(note).toContain("margin");
    expect(note).toContain("leverage");
    expect(note).toContain("live orders");
    expect(note).toContain("Sub-accounts".toLowerCase());
  });

  it("shows no unaccounted line, because the parts add up by construction", async () => {
    fleetData.mockReturnValue(fleet([controller({ global_pnl_quote: 12 })]));
    await render([agent()]);
    expect(pick("[data-floor-unaccounted]")).toBeNull();
  });
});

describe("the residual", () => {
  it("gets its own named row with a lead when no listed agent claims it", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ bot_name: "alpha-mm-1", global_pnl_quote: 10, volume_traded: 5 }),
        // Attributed to `ghost.mm` — a run key whose agent is not in `/agents`.
        controller({ bot_name: "ghost-mm-1", global_pnl_quote: 7, volume_traded: 3 }),
      ]),
    );
    await render([agent()]);

    const row = pick('[data-floor-row="ghost.mm"]')!;
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("claimed by no listed agent");
    expect(row.querySelector("[data-floor-lead]")).toBeTruthy();
    expect(row.querySelector("a")!.getAttribute("href")).toBe(
      "/bots?scope=agent%3Aghost.mm",
    );
    // And the strip still equals the whole, residual included.
    expect(pick("[data-floor-net]")!.textContent).toContain("+$17.00");
  });

  it("names the unowned buckets apart rather than lumping them together", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ bot_name: "nobody-1", global_pnl_quote: 3, deployed_at: "2026-09-04T09:00:00Z" }),
      ]),
    );
    await render([agent()]);
    expect(text()).toMatch(/Outside Condor|Before the ledger/);
  });
});

describe("the breakdowns", () => {
  it("cut the same records by instrument and by venue", async () => {
    fleetData.mockReturnValue(
      fleet([
        controller({ trading_pair: "SOL-USDC", connector: "binance", global_pnl_quote: 10 }),
        controller({ trading_pair: "BTC-USDT", connector: "kucoin", global_pnl_quote: -4 }),
      ]),
    );
    await render([agent()]);

    const pairs = container.querySelectorAll('[data-floor-breakdown="pair"] [data-floor-bucket]');
    const venues = container.querySelectorAll('[data-floor-breakdown="venue"] [data-floor-bucket]');
    expect(pairs).toHaveLength(2);
    expect(venues).toHaveLength(2);
    expect(
      pick('[data-floor-breakdown="venue"]')!.textContent,
    ).toContain("Leverage and margin health are not measured");
  });
});

describe("the page", () => {
  it("owns its own scrolling", async () => {
    await render([agent()]);
    expect(container.querySelector(".overflow-y-auto")).toBeTruthy();
  });

  it("folds nothing at all when no server has been chosen", async () => {
    // No declared server anywhere in the chain and no ambient one: the agent has
    // no fleet to fold, so it gets no row rather than somebody else's numbers.
    await render([agent({ server_name: "" })], null);
    expect(pick('[data-floor-row="alpha"]')).toBeNull();
    expect(pick("[data-floor-empty]")).toBeTruthy();
  });

  it("says the chart has no history rather than drawing an empty one", async () => {
    fleetData.mockReturnValue(fleet([controller({ global_pnl_quote: 1 })]));
    await render([agent()]);
    expect(pick("[data-floor-chart-empty]")).toBeTruthy();
  });
});

describe("the chart", () => {
  /** One owned controller with history, and one that declares no capital. */
  function charted() {
    fleetData.mockReturnValue(
      fleet(
        [
          controller({
            controller_id: "c1",
            bot_name: "alpha-mm-1",
            global_pnl_quote: 30,
            realized_pnl_quote: 30,
            config: { total_amount_quote: 1000 },
          } as Partial<ControllerInfo>),
        ],
        [
          snap("alpha-mm-1", "c1", "2026-09-04T10:00:00Z", 10),
          snap("alpha-mm-1", "c1", "2026-09-04T10:05:00Z", 20),
        ],
      ),
    );
  }

  it("draws a legend entry per agent, with the Total beside it", async () => {
    charted();
    await render([agent()]);
    expect(pick("[data-floor-legend=\"total\"]")).toBeTruthy();
    expect(pick("[data-floor-legend=\"alpha\"]")!.textContent).toContain("Alpha");
    expect(pick("[data-floor-chart-empty]")).toBeNull();
  });

  it("takes its view state from the URL and falls back rather than throwing", async () => {
    charted();
    await render([agent()], "s1", "/floor?basis=rel&from=window&range=nonsense");
    expect(pick('[data-floor-toggle="rel"]')!.dataset.active).toBe("true");
    expect(pick('[data-floor-toggle="window"]')!.dataset.active).toBe("true");
    // A stale `?range=` lands on the page that was asked for, not on an error.
    expect(pick('[data-floor-toggle="all"]')!.dataset.active).toBe("true");
  });

  it("lists an owner with no declared capital in Relative, and does not plot it", async () => {
    fleetData.mockReturnValue(
      fleet(
        [
          controller({
            controller_id: "c1",
            bot_name: "alpha-mm-1",
            global_pnl_quote: 30,
          }),
        ],
        [
          snap("alpha-mm-1", "c1", "2026-09-04T10:00:00Z", 10),
          snap("alpha-mm-1", "c1", "2026-09-04T10:05:00Z", 20),
        ],
      ),
    );
    await render([agent()], "s1", "/floor?basis=rel");

    const note = pick("[data-floor-unplottable]")!;
    expect(note.textContent).toContain("no declared capital");
    expect(note.textContent).toContain("Alpha");
    expect(text()).not.toContain("Infinity");
    expect(text()).not.toContain("NaN");
  });
});

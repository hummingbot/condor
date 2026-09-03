/**
 * The workspace's fleet, rooted at the agent (FEAT-108).
 *
 * The three claims that make this a *rooted* browser rather than a link to
 * `/bots`: only the agent's records are on screen, no scope in the URL can
 * reach anyone else's, and the line above it says how much of the fleet is
 * being left out. The fourth is the server: an agent whose bots run somewhere
 * else has a fleet, and it is read from the agent's own server rather than from
 * the ambient one.
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

import { ServerContext } from "@/hooks/useServer";
import type { AgentRunRow, ControllerInfo } from "@/lib/api";

const getBots = vi.fn();
const getExecutorsPage = vi.fn();
const getFleetMap = vi.fn();
const getStrategySessionExecutors = vi.fn();

vi.mock("@/lib/api", () => {
  const named: Record<string, (...args: unknown[]) => unknown> = {
    getBots: (...a) => getBots(...a),
    getExecutorsPage: (...a) => getExecutorsPage(...a),
    getFleetMap: (...a) => getFleetMap(...a),
    getStrategySessionExecutors: (...a) => getStrategySessionExecutors(...a),
    getBotRuns: () => Promise.resolve({ runs: [], total: 0 }),
    getTerminatedControllers: () => Promise.resolve({ controllers: [], runs_seen: 0 }),
  };
  return {
    api: new Proxy(named, {
      get: (target, key: string) => target[key] ?? (() => Promise.resolve({})),
    }),
  };
});

// The chart subsystems, the editors and the dialogs are whole worlds of their
// own and none of them is about which records are in scope.
vi.mock("@/components/bots/ControllerPnlChart", () => ({ ControllerPnlChart: () => null }));
vi.mock("@/components/bots/PnlEvolutionChart", () => ({ PnlEvolutionChart: () => null }));
vi.mock("@/components/charts/ExecutorChart", () => ({ ExecutorChart: () => null }));
vi.mock("@/components/editor/EditorModal", () => ({ EditorModal: () => null }));
vi.mock("@/components/bots/LogsSection", () => ({ LogsSection: () => null }));
vi.mock("@/components/bots/DeployBotDialog", () => ({ DeployBotDialog: () => null }));
vi.mock("@/components/bots/ArchivedBotDetail", () => ({ ArchivedBotDetail: () => null }));
vi.mock("@/components/perf/YamlConfigEditor", () => ({ YamlConfigEditor: () => null }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));
vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    rates: {},
    convert: (v: number) => ({ value: v }),
    formatValue: (v: number) => `$${v}`,
    formatPnlValue: (v: number) => `$${v}`,
    formatValueDetailed: (v: number) => `$${v}`,
    isLoading: false,
    currency: "USD",
    currencySymbol: "$",
    resolvedSymbol: "$",
    usdConverted: true,
  }),
}));

const { AgentFleet } = await import("./AgentFleet");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();

function controllerOf(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "pmm-1",
    bot_name: "brigado-brl_mm-btc",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 2,
    global_pnl_quote: 12,
    global_pnl_pct: 1.5,
    volume_traded: 100,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: HOUR_AGO,
    config: {},
    ...over,
  };
}

function ownerOf(slug: string, sslug: string) {
  return {
    runKey: `${slug}.${sslug}`,
    agentSlug: slug,
    agentName: slug,
    strategySlug: sslug,
    strategyName: sslug,
    namespace: `${slug}-${sslug}`,
    declaredBots: [],
    agentIds: [],
    live: null,
  };
}

/** Brigado's bot and Vega's, on the same server. */
const FLEET = {
  bots: [
    { bot_name: "brigado-brl_mm-btc", deployed_at: HOUR_AGO },
    { bot_name: "vega-momentum-eth", deployed_at: HOUR_AGO },
  ],
  controllers: [
    controllerOf(),
    controllerOf({ bot_name: "vega-momentum-eth", controller_id: "grid-9" }),
  ],
  server_online: true,
};

const MAP = {
  owners: [ownerOf("brigado", "brl_mm"), ownerOf("vega", "momentum")],
  deeds: { bots: {}, since: 0 },
};

let container: HTMLDivElement;
let root: Root;
let switched: string[];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // The sidebar scrolls the active scope into view on mount; jsdom has no
  // layout and therefore no `scrollIntoView`.
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  switched = [];
  getBots.mockReset();
  getExecutorsPage.mockReset();
  getFleetMap.mockReset();
  getStrategySessionExecutors.mockReset();
  getStrategySessionExecutors.mockResolvedValue({ deployments: [] });
  getBots.mockResolvedValue(FLEET);
  getExecutorsPage.mockResolvedValue({ executors: [], next_cursor: null });
  getFleetMap.mockResolvedValue(MAP);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(
  search = "",
  {
    serverName = "",
    ambient = "dashboard-server",
    run = null as AgentRunRow | null,
  } = {},
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/agents/brigado?view=fleet${search}`]}>
          <ServerContext.Provider
            value={{ server: ambient, setServer: (s) => switched.push(s) }}
          >
            <AgentFleet
              slug="brigado"
              sslug="brl_mm"
              serverName={serverName}
              run={run}
            />
          </ServerContext.Provider>
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

const text = () => container.textContent ?? "";

/** A live session run of Brigado's loop, as the loop bar hands it over. */
function session(number: number): AgentRunRow {
  return {
    run_id: `s${number}`,
    kind: "session",
    number,
    agent_id: `brigado.brl_mm_${number}`,
    status: "running",
    execution_mode: "",
    tick_count: 4,
    snapshot_count: 4,
    started_at: Math.floor(Date.now() / 1000) - 3600,
    ended_at: null,
    error: false,
    has_actions_log: true,
    strategy_slug: "brl_mm",
    strategy_name: "brl_mm",
  };
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined;
}

describe("AgentFleet", () => {
  it("draws the browser rooted at the agent, and nobody else's records", async () => {
    await render();

    expect(text()).toContain("brigado-brl_mm-btc");
    expect(text()).toContain("pmm-1");
    // Vega's bot is in the same fleet map, on the same server, and is not on
    // screen: the root is a floor, so the tree is drawn from it.
    expect(text()).not.toContain("vega-momentum-eth");
  });

  // The escape the clamp exists to close: a hand-typed scope naming another
  // agent's controller.
  it("clamps a scope pointing outside the agent back to the agent", async () => {
    await render("&fscope=ctrl%3Avega-momentum-eth%3Agrid-9");

    expect(text()).toContain("brigado-brl_mm-btc");
    expect(text()).not.toContain("vega-momentum-eth");
    expect(text()).not.toContain("grid-9");
  });

  // `?scope=` belongs to `/bots` and means nothing here — reading it would be
  // the two grammars fighting over one key.
  it("ignores the page's own scope parameter", async () => {
    await render("&scope=ctrl%3Avega-momentum-eth%3Agrid-9");

    expect(text()).not.toContain("vega-momentum-eth");
  });

  // The rule FEAT-108 left for FEAT-107: whatever order the reader picks, the
  // browser draws the level its root lives on. A grouping with no owner level
  // would leave the floor no node to be, and a rooted host would silently
  // report an empty fleet.
  it("keeps the agent's own level whatever the grouping asks for", async () => {
    for (const groupBy of ["pair", "ctrlType", "bot", "none"]) {
      await render(`&groupBy=${groupBy}`);

      expect(text(), `empty fleet under ?groupBy=${groupBy}`).toContain("brigado-brl_mm-btc");
      expect(text(), `escaped the root under ?groupBy=${groupBy}`).not.toContain(
        "vega-momentum-eth",
      );
      await act(async () => root.unmount());
      root = createRoot(container);
    }
  });

  it("says how much of the fleet it is leaving out", async () => {
    await render();

    expect(text()).toContain("Showing 1 of 2 controllers");
    expect(text()).toContain("brigado / brl_mm");
  });

  it("reads the agent's own server, and offers to move the app to it", async () => {
    await render("", { serverName: "brigado_2", ambient: "dashboard-server" });

    expect(getBots).toHaveBeenCalledWith("brigado_2");
    expect(text()).toContain("brigado_2");
    button("switch")!.click();
    expect(switched).toEqual(["brigado_2"]);
  });

  it("does not nag when the agent's server is the ambient one", async () => {
    await render("", { serverName: "dashboard-server", ambient: "dashboard-server" });

    expect(getBots).toHaveBeenCalledWith("dashboard-server");
    expect(button("switch")).toBeUndefined();
  });

  // The join FEAT-101 and FEAT-103 were each half of: the loop bar picks a run,
  // and the fleet shows what that run put into the world.
  it("narrows to the run the loop bar has selected", async () => {
    getStrategySessionExecutors.mockResolvedValue({
      deployments: [{ kind: "bot", label: "brigado-brl_mm-eth", scope: "bot:brigado-brl_mm-eth" }],
    });

    await render("", { run: session(3) });

    expect(getStrategySessionExecutors).toHaveBeenCalledWith("brigado", "brl_mm", 3);
    expect(text()).toContain("run S3 only");
    // Run 3 deployed a bot that is not this one, so this one is out of scope.
    expect(text()).not.toContain("brigado-brl_mm-btc");
  });

  // The run is passed in rather than read from `?run=`, so the chip's × cannot
  // write the URL — it has to be a control the host owns.
  it("gives the run filter a way out that does not move the loop bar", async () => {
    getStrategySessionExecutors.mockResolvedValue({
      deployments: [{ kind: "bot", label: "brigado-brl_mm-eth", scope: "bot:brigado-brl_mm-eth" }],
    });

    await render("", { run: session(3) });
    const clear = container.querySelector<HTMLButtonElement>(
      '[aria-label="Clear the run filter"]',
    );
    expect(clear).toBeTruthy();
    await act(async () => clear!.click());

    expect(text()).not.toContain("run S3 only");
    expect(text()).toContain("brigado-brl_mm-btc");
  });

  it("says so rather than drawing an empty fleet when there is no server at all", async () => {
    await render("", { serverName: "", ambient: null as unknown as string });

    expect(text()).toContain("no server pinned");
  });
});

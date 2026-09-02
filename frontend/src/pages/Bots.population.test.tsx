/**
 * Which page `/bots` draws when the live fleet is empty (CORR-297).
 *
 * The empty state used to be gated on the *live* controller list alone, and
 * `PerfBrowser` returned `null` for the same reason — so a server whose bots
 * had all stopped answered "No bots running" for `?population=terminated`
 * too, and the run history, the closed executors and the archive drill-in
 * (which `?tab=runs`, `?tab=archived` and the legacy `/executors` routes all
 * redirect into) were unreachable from the UI. An empty fleet is exactly when
 * the terminated population is worth reading.
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
import type { BotRunInfo, ControllerInfo } from "@/lib/api";

const getBots = vi.fn();
const getBotRuns = vi.fn();
const getTerminatedControllers = vi.fn();
const getExecutorsPage = vi.fn();

// Everything the browser asks for beyond the four queries this case is about
// answers empty: none of it decides whether the page is drawn.
vi.mock("@/lib/api", () => {
  const named: Record<string, (...args: unknown[]) => unknown> = {
    getBots: (...a) => getBots(...a),
    getBotRuns: (...a) => getBotRuns(...a),
    getTerminatedControllers: (...a) => getTerminatedControllers(...a),
    getExecutorsPage: (...a) => getExecutorsPage(...a),
  };
  return {
    api: new Proxy(named, {
      get: (target, key: string) => target[key] ?? (() => Promise.resolve({})),
    }),
  };
});

// The chart subsystems, the editors and the dialogs are whole worlds of their
// own and none of them is about which population is on screen.
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
    convert: (v: number) => v,
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

const { Bots } = await import("./Bots");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();

function terminatedControllerOf(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "grid_strike",
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
    ...over,
  };
}

function runOf(over: Partial<BotRunInfo> = {}): BotRunInfo {
  return {
    bot_name: "mm-sol-1",
    bot_run_id: 7,
    account_name: "master",
    strategy_type: "generic",
    strategy_name: "grid_strike",
    run_status: "STOPPED",
    deployment_status: "STOPPED",
    created_at: HOUR_AGO,
    stopped_at: new Date(Date.now() - 60_000).toISOString(),
    realized_pnl_quote: 12,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 12,
    volume_traded: 5000,
    num_controllers: 0,
    archive_db_path: null,
    controller_ids: ["grid-alpha"],
    is_live: false,
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // The sidebar scrolls the active scope into view on mount; jsdom has no
  // layout and therefore no `scrollIntoView`.
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getBots.mockReset();
  getBotRuns.mockReset();
  getTerminatedControllers.mockReset();
  getExecutorsPage.mockReset();
  // The fleet is empty — that is the whole point of these cases.
  getBots.mockResolvedValue({ bots: [], controllers: [], server_online: true });
  getBotRuns.mockResolvedValue({ runs: [], total: 0 });
  getTerminatedControllers.mockResolvedValue({ controllers: [], runs_seen: 0 });
  getExecutorsPage.mockResolvedValue({ executors: [], next_cursor: null });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(search: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/bots${search}`]}>
          <ServerContext.Provider value={{ server: "dashboard-server", setServer: () => {} }}>
            <Bots />
          </ServerContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // The fleet, runs and terminated-controller fetches land over a few ticks.
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const text = () => container.textContent ?? "";

/** A button by its visible label. */
function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined;
}

describe("/bots with no live fleet", () => {
  it("draws the terminated population instead of the empty state", async () => {
    getTerminatedControllers.mockResolvedValue({
      controllers: [terminatedControllerOf()],
      runs_seen: 1,
    });
    getBotRuns.mockResolvedValue({ runs: [runOf()], total: 1 });

    await render("?population=terminated");

    // The finished run's bot and the controller it left behind are both on
    // screen — the browser was rendered, not the fleet's empty state.
    expect(text()).toContain("mm-sol-1");
    expect(text()).toContain("grid-alpha");
    expect(text()).not.toContain("No bots running");
  });

  it("keeps the population toggle reachable when the terminated set is empty too", async () => {
    // PerfBrowser used to return `null` on an empty fleet, which stranded the
    // reader on a blank page with no way back to Running.
    await render("?population=terminated");

    expect(button("Running")).toBeTruthy();
    expect(text()).toContain("Nothing in scope.");
    expect(text()).not.toContain("No bots running");
  });

  it("still offers Deploy on the Running population", async () => {
    await render("");

    expect(text()).toContain("No bots running");
    expect(button("Deploy Bot")).toBeTruthy();
  });
});

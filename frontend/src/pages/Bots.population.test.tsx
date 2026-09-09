/**
 * Which page `/bots` draws when the live fleet is empty (CORR-297, CORR-357).
 *
 * The empty state used to be gated on the *live* controller list alone, and
 * `PerfBrowser` returned `null` for the same reason — so a server whose bots
 * had all stopped answered "No bots running" for `?population=terminated`
 * too, and the run history, the closed executors and the archive drill-in
 * (which `?tab=runs`, `?tab=archived` and the legacy `/executors` routes all
 * redirect into) were unreachable from the UI. An empty fleet is exactly when
 * the terminated population is worth reading.
 *
 * CORR-297 fixed one direction only: the *running* side kept the standalone
 * deploy-only page, which strands the reader just as badly. Nothing in the app
 * links to `?population=terminated` (the `?tab=` and `/executors` routes are
 * redirects), so on a fleet whose bots had all stopped the population toggle
 * was gone with the browser and the history was reachable only by editing the
 * URL — and because the running population counts active *executors* besides
 * controllers, a standalone executor holding open capital was drawn as "No bots
 * running". So the browser is now the page in both populations, and the only
 * thing the page still says for itself is the broker diagnostic below.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { BotRunInfo, BotSummary, ControllerInfo, ExecutorInfo } from "@/lib/api";

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
    // A list, not the proxy's `{}`: the fleet map is a `FleetOwner[]` and the
    // page iterates it. No agents is the case this file is about anyway.
    getFleetMap: () => Promise.resolve({ owners: [], deeds: { bots: {}, since: 0 } }),
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
    convert: (v: number) => ({ value: v, converted: true }),
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

/** An active executor nobody claims: no `controller_id`, so it files under
 *  `(unattached)` — the running population counts it even with no controllers. */
function activeExecutorOf(over: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "ex-solo-1",
    type: "position_executor",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 12,
    volume: 5000,
    timestamp: Date.parse(HOUR_AGO) / 1000,
    controller_id: "",
    cum_fees_quote: 0,
    net_pnl_pct: 0.004,
    entry_price: 100,
    current_price: 101,
    close_timestamp: 0,
    custom_info: {},
    config: {},
    ...over,
  };
}

function botOf(over: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_name: "mm-sol-1",
    status: "running",
    num_controllers: 0,
    error_count: 0,
    deployed_at: HOUR_AGO,
    error_logs: [],
    general_logs: [],
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

/** Where the router is, so a click on a population segment can be checked. */
function LocationProbe() {
  const { search } = useLocation();
  return <span data-testid="location">{search}</span>;
}

const locationSearch = () =>
  container.querySelector('[data-testid="location"]')?.textContent ?? "";

async function render(search: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/bots${search}`]}>
          <ServerContext.Provider value={{ server: "dashboard-server", setServer: () => {} }}>
            <Bots />
            <LocationProbe />
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
    // Locks the fix in: with a correctly-shaped `convert` mock, the folded
    // Realized ($12 → "+$12.00") and Volume ($5,000 → "$5.0K") tiles are real
    // money, not `NaN`.
    expect(text()).toContain("+$12.00");
    expect(text()).toContain("$5.0K");
  });

  it("keeps the population toggle reachable when the terminated set is empty too", async () => {
    // PerfBrowser used to return `null` on an empty fleet, which stranded the
    // reader on a blank page with no way back to Running.
    await render("?population=terminated");

    expect(button("Running")).toBeTruthy();
    expect(text()).toContain("Nothing in scope.");
    expect(text()).not.toContain("No bots running");
  });

  it("draws the browser on an empty running fleet, not a deploy-only page", async () => {
    await render("");

    // Both sides of the population toggle are on screen, with Running on: the
    // deploy-only page carried neither, so the terminated tree was reachable
    // only by hand-editing the URL (CORR-357).
    expect(button("Running")?.getAttribute("aria-pressed")).toBe("true");
    expect(button("Terminated")).toBeTruthy();
    // And the two fleet-wide actions the old page existed to carry: the
    // browser's own header has them, lowercase.
    expect(button("Deploy bot")).toBeTruthy();
    expect(button("Editor")).toBeTruthy();
  });

  it("reaches the terminated tree from an empty running fleet in one click", async () => {
    await render("");

    await act(async () => {
      button("Terminated")!.click();
    });

    expect(locationSearch()).toContain("population=terminated");
    expect(button("Terminated")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("draws an active executor that no controller claims", async () => {
    // Zero controllers, one executor holding open capital. The old gate counted
    // controllers only, so this rendered as "No bots running" while the money
    // was live — and the execution dock links here to show it.
    getExecutorsPage.mockResolvedValue({
      executors: [activeExecutorOf()],
      next_cursor: null,
    });

    await render("");

    expect(text()).not.toContain("No bots running");
    // It has a row of its own in the scope tree, filed under the unattached bot,
    // and the grain strip counts it: zero controllers, one executor.
    expect(text()).toContain("unattached");
    expect(text()).toContain("0 controllers · 1 executor");
    // Its money folded into the fleet strip: +$12 net over $5,000 of volume.
    expect(text()).toContain("+$12.00");
    expect(text()).toContain("$5.0K");
  });

  it("keeps the broker diagnostic and the population toggle together", async () => {
    // A bot the server can see, reporting no controller, means the MQTT reports
    // are not arriving. That diagnostic is the one thing the page still says for
    // itself — and it now says it *over* the browser rather than instead of it.
    getBots.mockResolvedValue({
      bots: [botOf()],
      controllers: [],
      server_online: true,
    });

    await render("");

    expect(text()).toContain("mm-sol-1 is running but reporting no controllers");
    expect(text()).toContain("make doctor");
    expect(button("Terminated")).toBeTruthy();
  });
});

/**
 * Reading `/bots` along an axis other than ownership (FEAT-107).
 *
 * The page's default is owner-first, which is the whole re-orientation. What
 * these cases pin is that it is a *default* and not the only reading: the
 * picker writes `?groupBy=`, the tree regroups, and — the claim that makes a
 * grouping trustworthy at all — the fleet adds up to the same money whichever
 * way it is nested. A fold that depended on the nesting would be a page whose
 * total moved when the reader changed the subject.
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
import type { ControllerInfo } from "@/lib/api";

const getBots = vi.fn();

vi.mock("@/lib/api", () => {
  const named: Record<string, (...args: unknown[]) => unknown> = {
    getBots: (...a) => getBots(...a),
    getBotRuns: () => Promise.resolve({ runs: [], total: 0 }),
    getTerminatedControllers: () => Promise.resolve({ controllers: [], runs_seen: 0 }),
    getExecutorsPage: () => Promise.resolve({ executors: [], next_cursor: null }),
    getFleetMap: () => Promise.resolve({ owners: [], deeds: { bots: {}, since: 0 } }),
  };
  return {
    api: new Proxy(named, {
      get: (target, key: string) => target[key] ?? (() => Promise.resolve({})),
    }),
  };
});

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

const { Bots } = await import("./Bots");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();

function controllerOf(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "pmm-1",
    bot_name: "alpha",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 10,
    global_pnl_pct: 1,
    volume_traded: 100,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: HOUR_AGO,
    config: {},
    ...over,
  };
}

/** Two bots, two pairs, two classes: every axis has something to tell apart. */
const FLEET = {
  bots: [
    { bot_name: "alpha", deployed_at: HOUR_AGO },
    { bot_name: "beta", deployed_at: HOUR_AGO },
  ],
  controllers: [
    controllerOf(),
    controllerOf({
      bot_name: "beta",
      controller_id: "grid-9",
      controller_name: "grid_strike",
      trading_pair: "BTC-USDT",
      global_pnl_quote: 25,
      realized_pnl_quote: 25,
    }),
  ],
  server_online: true,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getBots.mockReset();
  getBots.mockResolvedValue(FLEET);
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
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const text = () => container.textContent ?? "";

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined;
}

/** Whether the sidebar draws a row whose own name is exactly this. */
function hasRow(label: string): boolean {
  return [...container.querySelectorAll("button")].some((b) =>
    (b.textContent ?? "").startsWith(label),
  );
}

describe("/bots, grouped", () => {
  it("offers the four readings, with owner the one it opens on", async () => {
    await render("");

    for (const label of ["Owner", "Bot", "Pair", "Type"]) {
      expect(button(label), `no ${label} button`).toBeTruthy();
    }
    expect(button("Owner")!.getAttribute("aria-pressed")).toBe("true");
    // The bot level, and no pair level.
    expect(hasRow("alpha")).toBe(true);
    expect(hasRow("beta")).toBe(true);
    expect(hasRow("SOL-USDC")).toBe(false);
    // Nothing on this fleet is attributed and no deed index reaches back, so
    // every record falls in *one* owner bucket — and the row is drawn anyway.
    // The reader pressed Owner: the bucket is where the fleet's trading is
    // added up under whoever made it, and "they all agree" is the case where
    // that total is easiest to read, not a reason to withhold it.
    expect(text()).toContain("Before the ledger");
    expect(hasRow("alpha")).toBe(true);
  });

  it("regroups by pair when the link says so, without touching the fleet total", async () => {
    await render("");
    // 10 + 25, drawn by the fleet row's own fold.
    expect(text()).toContain("$35");
    const byOwner = text();

    await act(async () => root.unmount());
    root = createRoot(container);
    await render("?groupBy=pair");

    expect(button("Pair")!.getAttribute("aria-pressed")).toBe("true");
    // One row per pair, and the bots are gone from the level they had.
    expect(hasRow("SOL-USDC+$10.00")).toBe(true);
    expect(hasRow("BTC-USDT+$25.00")).toBe(true);
    expect(hasRow("alpha")).toBe(false);
    // The nesting is a reading order; it cannot move the money.
    expect(text()).toContain("$35");
    expect(byOwner).toContain("$35");
  });

  it("writes the reader's choice into the URL, and the default out of it", async () => {
    await render("");

    await act(async () => button("Pair")!.click());
    expect(button("Pair")!.getAttribute("aria-pressed")).toBe("true");
    expect(hasRow("SOL-USDC+$10.00")).toBe(true);

    await act(async () => button("Owner")!.click());
    expect(button("Owner")!.getAttribute("aria-pressed")).toBe("true");
    expect(hasRow("alpha")).toBe(true);
    expect(hasRow("SOL-USDC+$10.00")).toBe(false);
  });

  // A link written under one reading, opened under another. The scope names a
  // level this nesting does not have, and the page degrades to the report that
  // contains it rather than erroring or emptying.
  it("degrades a scope the new grouping has no node for", async () => {
    await render("?groupBy=pair&scope=bot%3Abeta");

    expect(hasRow("SOL-USDC+$10.00")).toBe(true);
    expect(hasRow("BTC-USDT+$25.00")).toBe(true);
    expect(text()).not.toContain("Nothing in scope.");
    // Landed on the fleet, which is the report the lost level was inside of.
    expect(text()).toContain("$35");
  });
});

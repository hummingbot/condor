/**
 * What an archived run's money reads as (CORR-288).
 *
 * The page used to carry two private formatters — a `formatUsd` that compacted
 * at 1K and hardcoded `$`, and a `formatPnl` wrapped around it — so the same
 * run read one way here and another in the browser beside it, and an
 * unconverted run was labelled in dollars it had never traded in. Both are
 * gone; these cases pin what replaced them.
 *
 * The sign is the point. `-410` must keep its minus: the sibling shadow in the
 * deleted `BotRunsTab` ran the amount through `Math.abs` and left a loss
 * indistinguishable from a gain of the same size.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { ArchivedBotPerformance, ArchivedExecutor } from "@/lib/api";

const getArchivedBotPerformance = vi.fn();
const getArchivedExecutors = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getArchivedBotPerformance: (...a: unknown[]) => getArchivedBotPerformance(...a),
    getArchivedExecutors: (...a: unknown[]) => getArchivedExecutors(...a),
    getArchivedControllers: () => Promise.resolve({ controllers: [] }),
  },
}));

// The chart panel is a whole subsystem (a routine run behind a lookup) and none
// of it is about how a number is spelled.
vi.mock("@/hooks/useArchivedReport", () => ({
  useArchivedReport: () => ({
    reportId: null,
    createdAt: null,
    title: "",
    isLoading: false,
    isRunning: false,
    error: null,
    chart: () => {},
    regenerate: () => {},
  }),
}));

vi.mock("@/components/routines/ReportFrame", () => ({
  ReportFrame: () => null,
}));

const { ArchivedBotDetail } = await import("./ArchivedBotDetail");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function perfOf(over: Partial<ArchivedBotPerformance> = {}): ArchivedBotPerformance {
  return {
    bot_name: "mm-sol-2",
    db_path: "/archived/mm-sol-2.sqlite",
    total_pnl: 0,
    total_fees: 0,
    total_volume: 0,
    trade_count: 0,
    buy_count: 0,
    sell_count: 0,
    pnl_by_pair: {},
    cumulative_pnl: [],
    trading_pairs: [],
    exchanges: [],
    executors: [],
    primary_connector: "binance",
    primary_trading_pair: "SOL-USDC",
    executor_count: 0,
    quote_currency: "USDC",
    usd_rates: { USDC: 1 },
    converted: true,
    stats_source: "trades",
    ...over,
  };
}

/**
 * One archived executor row (backend `NormalizedExecutor`).
 *
 * `usd_rate` is required, not optional: the archived routes always send it —
 * Pydantic defaults the field to 1.0 and `QuoteRates.for_pair` falls back to
 * 1.0 for a quote with no path to USD. The live `ExecutorInfo` has no such
 * field at all, so a live row cannot stand in here. That split is the point.
 */
function execOf(over: Partial<ArchivedExecutor> = {}): ArchivedExecutor {
  return {
    id: "abcdef0123456789",
    type: "position_executor",
    connector: "binance",
    trading_pair: "BTC-BRL",
    side: "BUY",
    status: "TERMINATED",
    close_type: "TAKE_PROFIT",
    pnl: 0,
    volume: 0,
    timestamp: 1_700_000_000,
    controller_id: "main",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 0,
    current_price: 0,
    close_timestamp: 1_700_000_100,
    custom_info: {},
    config: {},
    usd_rate: 1,
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getArchivedBotPerformance.mockReset();
  getArchivedExecutors.mockReset();
  getArchivedExecutors.mockResolvedValue({ executors: [], total: 0, offset: 0, limit: 50 });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(perf: ArchivedBotPerformance, executors: ArchivedExecutor[] = []) {
  getArchivedBotPerformance.mockResolvedValue(perf);
  getArchivedExecutors.mockResolvedValue({
    executors,
    total: executors.length,
    offset: 0,
    limit: 50,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "dashboard-server", setServer: () => {} }}>
          <ArchivedBotDetail dbPath="/archived/mm-sol-2.sqlite" botName="mm-sol-2" onBack={() => {}} />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  // The performance fetch lands a tick after mount.
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** A stat card's value, by the label above it. */
function statValue(label: string): string | undefined {
  const card = [...container.querySelectorAll("p")].find((p) => p.textContent?.trim() === label);
  return card?.parentElement?.querySelector("p:last-of-type")?.textContent?.trim();
}

/** The run's headline figure. */
const totalPnl = () => statValue("Total PnL");

describe("an archived run's headline PnL", () => {
  it("keeps the minus sign on a loss", async () => {
    await render(perfOf({ total_pnl: -410 }));
    expect(totalPnl()).toBe("-$410.00");
  });

  it("renders a genuine zero as an amount, not a dash", async () => {
    await render(perfOf({ total_pnl: 0 }));
    expect(totalPnl()).toBe("+$0.00");
    expect(totalPnl()).not.toBe("—");
  });

  it("marks a gain with a plus and groups it", async () => {
    await render(perfOf({ total_pnl: 1234.56 }));
    expect(totalPnl()).toBe("+$1,234.56");
  });

  it("agrees with the browser beside it, which compacts at 10K not 1K", async () => {
    await render(perfOf({ total_pnl: 1234.56 }));
    // The old private formatter said "+$1.2K" here while the runs listing said
    // "+$1,234.56" for the same run.
    expect(totalPnl()).not.toBe("+$1.2K");
  });
});

describe("an archived run that could not be converted to USD", () => {
  it("labels its figures with its own quote currency, not dollars", async () => {
    await render(
      perfOf({
        total_pnl: -410,
        total_volume: 5_000,
        quote_currency: "BRL",
        usd_rates: {},
        converted: false,
      }),
    );
    expect(totalPnl()).toBe("R$-410.00");
    expect(statValue("Volume")).toBe("R$5.0K");
  });

  it("falls back to the quote's code when the dashboard has no symbol for it", async () => {
    await render(
      perfOf({ total_pnl: -410, quote_currency: "TRY", usd_rates: {}, converted: false }),
    );
    expect(totalPnl()).toBe("TRY -410.00");
  });

  it("still says dollars when the run was converted", async () => {
    await render(perfOf({ total_pnl: -410, quote_currency: "BRL", converted: true }));
    expect(totalPnl()).toBe("-$410.00");
  });
});

/** The executor table's only row, cell by cell. */
function executorCells(): string[] {
  const row = container.querySelector("tbody tr");
  return [...(row?.querySelectorAll("td") ?? [])].map((td) => td.textContent?.trim() ?? "");
}

/**
 * ARCH-311: `usd_rate` lives on the archived row type only.
 *
 * These rows used to be typed `ExecutorInfo` — the *live* wire model, which
 * carries no rate — so the page had to read `ex.usd_rate ?? 1` and no test
 * pinned what the rate does. The fallback is gone; these cases pin that the
 * per-row rate is still what turns a quote figure into a dollar one.
 */
describe("an archived executor row's money columns", () => {
  it("restates a non-dollar quote at the row's own usd_rate", async () => {
    await render(
      perfOf({
        executor_count: 1,
        quote_currency: "BRL",
        usd_rates: { BRL: 0.18 },
        converted: true,
      }),
      [execOf({ pnl: 1000, cum_fees_quote: 50, volume: 20_000, usd_rate: 0.18 })],
    );
    // Unscaled these read +$1,000.00 / $50.00 / $20.0K — the whole BRL/USD
    // rate of overstatement the per-row rate exists to remove.
    expect(executorCells().slice(-3)).toEqual(["+$180.00", "$9.00", "$3.6K"]);
  });

  it("leaves a dollar-quoted run alone at the backend's default rate of 1", async () => {
    await render(perfOf({ executor_count: 1, quote_currency: "USDC", usd_rates: { USDC: 1 } }), [
      execOf({ trading_pair: "SOL-USDC", pnl: 1000, cum_fees_quote: 50, volume: 20_000 }),
    ]);
    expect(executorCells().slice(-3)).toEqual(["+$1,000.00", "$50.00", "$20.0K"]);
  });
});

/**
 * Every money cell under a run's executors goes through the display-currency
 * rate formatters (CORR-389).
 *
 * `SessionExecutors` fetched `formatPnlValue` / `formatValue` /
 * `formatValueDetailed` from `useRates` but handed them only to `DetailPanel`:
 * the Positions Held table printed `${entry.toFixed(2)}` and a `$` PnL for
 * quote-currency numbers, the per-pair header used a bare `formatCurrencyPnl`,
 * and `ExecutorTable` fell back to its USD formatters. A BTC-BRL breakeven of
 * 312,000 BRL read `$312000.00`, and the currency switch changed none of them
 * while the detail panel one click away showed them converted. The rates it
 * asked for came from executor pairs only, so a position whose quote no
 * executor shared had no rate fetched at all.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentExecutorRow, PositionHeld } from "@/lib/api";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const state = vi.hoisted(() => ({
  executors: [] as AgentExecutorRow[],
  positions: [] as PositionHeld[],
}));

vi.mock("@/lib/api", () => ({
  api: {
    getStrategySessionExecutors: vi.fn(async () => ({ executors: state.executors })),
    getSessionSnapshots: vi.fn(async () => ({ snapshots: [] })),
    getPositionsHeld: vi.fn(async () => ({ positions: state.positions, summary: {} })),
  },
}));
vi.mock("@/hooks/useAgentExecutors", () => ({
  useAgentExecutors: () => ({ executors: [] }),
}));
vi.mock("@/hooks/useSnapshotBubbles", () => ({
  useSnapshotBubbles: () => [],
}));
vi.mock("@/components/charts/ExecutorChart", () => ({
  ExecutorChart: () => null,
}));
vi.mock("@/components/executor/PairLabel", () => ({
  PairLabel: ({ tradingPair }: { tradingPair: string }) => <span>{tradingPair}</span>,
}));

const rates = vi.hoisted(() => ({
  useRates: vi.fn(),
  formatPnlValue: vi.fn((val: number, quote?: string) => `PNL[${val} ${quote}]`),
  formatValue: vi.fn((val: number, quote?: string) => `VAL[${val} ${quote}]`),
  formatValueDetailed: vi.fn((val: number, quote?: string) => `DET[${val} ${quote}]`),
}));
vi.mock("@/hooks/useRates", () => ({
  useRates: (quotes: string[]) => {
    rates.useRates(quotes);
    return {
      // No rate for anything: the price path takes formatWithRate's
      // unconverted branch and keeps the quote's own symbol.
      rates: {},
      currency: "USD",
      formatPnlValue: rates.formatPnlValue,
      formatValue: rates.formatValue,
      formatValueDetailed: rates.formatValueDetailed,
    };
  },
}));

const { SessionExecutors } = await import("./SessionExecutors");

function row(id: string, connector: string, pair: string, pnl: number): AgentExecutorRow {
  return {
    id,
    type: "position_executor",
    connector,
    pair,
    side: "BUY",
    status: "terminated",
    close_type: "TAKE_PROFIT",
    pnl,
    volume: 1000,
    fees: 1,
    entry_price: 100,
    current_price: 101,
    amount: 1,
    timestamp: 1_700_000_000,
    close_timestamp: 1_700_000_100,
    controller_id: "ctrl-1",
  };
}

const brlPosition: PositionHeld = {
  connector_name: "binance",
  trading_pair: "BTC-BRL",
  side: "BUY",
  amount: 0.01,
  buy_breakeven_price: 312000,
  current_price: 318000,
  unrealized_pnl_quote: 60,
  controller_id: "ctrl-1",
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SessionExecutors slug="brigado" sslug="fleet" sessionNum={1} serverName="srv" controllerIds={["ctrl-1"]} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // Let the three queries resolve and re-render.
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function positionCells(): string[] {
  const card = Array.from(container.querySelectorAll("h3")).find((h) => h.textContent?.startsWith("Positions Held"));
  expect(card).toBeTruthy();
  return Array.from(card!.parentElement!.querySelectorAll("tbody td")).map((td) => td.textContent ?? "");
}

describe("SessionExecutors display currency", () => {
  it("renders a BRL position and a BRL executor through the rate formatters", async () => {
    state.executors = [row("ex-1", "binance", "BTC-BRL", 42)];
    state.positions = [brlPosition];
    await mount();

    const cells = positionCells();
    expect(cells[5]).toBe("PNL[60 BRL]");
    expect(rates.formatPnlValue).toHaveBeenCalledWith(60, "BRL");
    // No BRL rate: the price keeps the BRL symbol and the ⚠ marker at full precision.
    expect(cells[3]).toBe("R$312000.00 ⚠");
    expect(cells[4]).toBe("R$318000.00 ⚠");
    expect(cells.some((c) => c.startsWith("$"))).toBe(false);

    // The executor table's PnL cell uses the same formatter, in the row's quote.
    expect(rates.formatPnlValue).toHaveBeenCalledWith(42, "BRL");
    expect(container.textContent).toContain("PNL[42 BRL]");
  });

  it("fetches the position's quote even when no executor shares it, and converts each pair header", async () => {
    state.executors = [row("ex-1", "binance", "SOL-USDC", 5), row("ex-2", "okx", "ETH-USDT", -3)];
    state.positions = [brlPosition];
    await mount();

    const quoteSets = rates.useRates.mock.calls.map(([q]) => q as string[]);
    expect(quoteSets.some((q) => q.includes("BRL") && q.includes("USDC") && q.includes("USDT"))).toBe(true);

    // Two chart groups, so each gets a header PnL in its own quote.
    expect(rates.formatPnlValue).toHaveBeenCalledWith(5, "USDC");
    expect(rates.formatPnlValue).toHaveBeenCalledWith(-3, "USDT");
    const headers = Array.from(container.querySelectorAll("span.ml-auto")).map((s) => s.textContent);
    expect(headers).toEqual(expect.arrayContaining(["PNL[5 USDC]", "PNL[-3 USDT]"]));
  });
});

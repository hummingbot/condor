/**
 * What the Positions tab promises (FEAT-057).
 *
 * Four things are load-bearing and none of them are visual: a hold reads in the
 * *display* currency rather than its pair's quote; a row is a door to the
 * surface that owns its actions and nothing else — no close, clear or stop
 * lives here; an empty tab says it is empty instead of rendering nothing; and
 * an LP range older than the newest page of executors still shows up, which is
 * the regression this feature was designed around and the reason the liquidity
 * list comes from a *filtered* executor query rather than the page-wide cache
 * the portfolio already holds.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConsolidatedPosition, ExecutorInfo } from "@/lib/api";
import type { LpPosition } from "@/components/dex/lp-position";
import { useLpPositions } from "@/hooks/useLpPositions";
import { PositionsTab } from "./PositionsTab";

vi.mock("@/lib/api", () => ({
  api: {
    getExecutors: vi.fn(async (_server: string, params?: Record<string, unknown>) =>
      params?.executor_type === "lp_executor" ? LP_EXECUTORS : UNFILTERED_PAGE,
    ),
    getDexPoolsByAddress: vi.fn(async () => ({ pools: [] })),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let LP_EXECUTORS: ExecutorInfo[] = [];
let UNFILTERED_PAGE: ExecutorInfo[] = [];

// ── Fixtures ──

function hold(over: Partial<ConsolidatedPosition>): ConsolidatedPosition {
  return {
    connector_name: "binance_perpetual",
    trading_pair: "ETH-USDT",
    position_side: "LONG",
    amount: 1.5,
    entry_price: 3410,
    notional_value: 5115,
    current_price: 3502,
    unrealized_pnl: 138,
    realized_pnl: 0,
    cum_fees: 0,
    executor_count: 1,
    leverage: 5,
    controller_id: "",
    source: "executor",
    source_name: "",
    ...over,
  };
}

function lpExecutor(over: Partial<ExecutorInfo> & { id: string }): ExecutorInfo {
  return {
    type: "lp",
    status: "running",
    connector: "meteora/clmm",
    trading_pair: "So11111111111111111111111111111111111111112-USDC",
    pnl: 12.4,
    current_price: 141,
    config: {
      pool_address: "Pool123456",
      connector_name: "solana_mainnet-beta",
      lp_provider: "meteora",
    },
    custom_info: {
      state: "IN_RANGE",
      lower_price: 132.1,
      upper_price: 147.9,
      current_price: 141,
      total_value_quote: 1204.11,
      fees_earned_quote: 3.19,
    },
    ...over,
  } as unknown as ExecutorInfo;
}

function lpPosition(over: Partial<LpPosition> = {}): LpPosition {
  return {
    id: "lp-1",
    network: "solana_mainnet-beta",
    poolAddress: "Pool123456",
    provider: "meteora",
    pair: "SOL-USDC",
    state: "IN_RANGE",
    lowerPrice: 132.1,
    upperPrice: 147.9,
    currentPrice: 141,
    valueQuote: 1204.11,
    feesQuote: 3.19,
    pnl: 12.4,
    ...over,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;
let path = "";

function Location() {
  const loc = useLocation();
  // In an effect, not in render: the probe records where a click landed, and
  // writing to an outer binding mid-render is the side effect the lint forbids.
  useEffect(() => {
    path = loc.pathname + loc.search;
  }, [loc]);
  return null;
}

/** A euro-denominated stand-in for the page's rates, so a leak reads as `$`. */
const fmtValue = (val: number) => `€${val.toFixed(2)}`;
const fmtPnl = (val: number) => `${val >= 0 ? "+" : ""}€${val.toFixed(2)}`;

interface RenderOpts {
  holds?: ConsolidatedPosition[];
  lpPositions?: LpPosition[];
  isLoading?: boolean;
}

async function render(opts: RenderOpts = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/portfolio?tab=positions"]}>
          <Location />
          <PositionsTab
            holds={opts.holds ?? []}
            lpPositions={opts.lpPositions ?? []}
            lpLabel={(p) => p.pair}
            isLoading={opts.isLoading ?? false}
            // Halve the value, so a row that skipped conversion is visible.
            convert={(value) => ({ value: value / 2, converted: true })}
            formatValue={(val) => fmtValue(val / 2)}
            formatPnlValue={(val) => fmtPnl(val / 2)}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await flush();
}

/** react-query resolves on a later macrotask than the render that asked. */
async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const rows = (sel: string) => [...document.querySelectorAll<HTMLElement>(sel)];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  LP_EXECUTORS = [];
  UNFILTERED_PAGE = [];
  path = "";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("holds", () => {
  it("renders entry, mark and PnL in the display currency, not the pair's quote", async () => {
    await render({ holds: [hold({})] });

    const cells = rows("[data-hold-row] td").map((td) => td.textContent);
    expect(cells).toContain("€1705.00"); // entry 3410, converted
    expect(cells).toContain("€1751.00"); // mark 3502, converted
    expect(cells).toContain("+€69.00"); // unrealized 138, converted
    expect(document.body.textContent).not.toContain("$");
  });

  it("shows a size in base units, untouched by the rate", async () => {
    await render({ holds: [hold({ amount: 1.5 })] });
    expect(rows("[data-hold-row] td").map((td) => td.textContent)).toContain("1.5");
  });

  it("shows no leverage on a spot hold rather than claiming 1x", async () => {
    await render({ holds: [hold({ leverage: 1, position_side: "" })] });

    const text = document.querySelector("[data-hold-row]")!.textContent!;
    expect(text).not.toContain("1x");
    // An empty side is rendered as a dash, never guessed at.
    expect(text).not.toContain("LONG");
  });

  it("reads a side the API stringified out of a Python enum", async () => {
    await render({
      holds: [hold({ source: "bot", source_name: "bot-a/pmm", position_side: "TradeType.SELL" })],
    });

    const side = document.querySelector("[data-hold-row] td:nth-child(2)")!;
    expect(side.textContent).toBe("SELL");
    expect(side.textContent).not.toContain("TradeType");
  });

  it("attributes a bot hold to its bot and an executor hold to its executors", async () => {
    await render({
      holds: [
        hold({ trading_pair: "ETH-USDT", source: "bot", source_name: "bot-a/pmm" }),
        hold({ trading_pair: "SOL-USDC", executor_count: 3, notional_value: 10 }),
      ],
    });

    const text = rows("[data-hold-row]").map((r) => r.textContent!);
    expect(text.find((t) => t.includes("ETH-USDT"))).toContain("bot-a/pmm");
    expect(text.find((t) => t.includes("SOL-USDC"))).toContain("3 executors");
  });

  it("puts the biggest converted notional first", async () => {
    await render({
      holds: [
        hold({ trading_pair: "SMALL-USDT", notional_value: 10 }),
        hold({ trading_pair: "BIG-USDT", notional_value: -9000 }), // a short still counts
      ],
    });

    const pairs = rows("[data-hold-row]").map((r) => r.textContent!);
    expect(pairs[0]).toContain("BIG-USDT");
  });

  it("opens the trade workspace on the row's own connector and pair", async () => {
    await render({ holds: [hold({ connector_name: "binance_perpetual", trading_pair: "ETH-USDT" })] });

    await act(async () => document.querySelector<HTMLElement>("[data-hold-row]")!.click());

    expect(path).toBe("/trade?connector=binance_perpetual&pair=ETH-USDT");
  });
});

describe("liquidity", () => {
  it("opens that pool's workspace", async () => {
    await render({ lpPositions: [lpPosition()] });

    await act(async () => document.querySelector<HTMLElement>("[data-lp-row]")!.click());

    expect(path).toBe("/dex/solana_mainnet-beta/Pool123456");
  });

  it("names an out-of-range position, which is the row the section exists for", async () => {
    await render({ lpPositions: [lpPosition({ state: "OUT_OF_RANGE" })] });
    expect(document.querySelector("[data-lp-row]")!.textContent).toContain("Out of range");
  });
});

describe("the tab never mutates anything", () => {
  it("offers no close, clear or stop control on any row", async () => {
    await render({ holds: [hold({})], lpPositions: [lpPosition()] });

    // Rows navigate; they are the only interactive thing on the page.
    expect(rows("[data-hold-row] button, [data-lp-row] button")).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/close|clear|stop/i);
  });
});

describe("empty and loading", () => {
  it("says it is empty rather than rendering nothing", async () => {
    await render({});

    expect(container.innerHTML).not.toBe("");
    expect(document.body.textContent).toContain("No open positions.");
    expect(rows("table")).toHaveLength(0);
  });

  it("omits the section that has nothing under it", async () => {
    await render({ lpPositions: [lpPosition()] });

    expect(document.body.textContent).toContain("Liquidity");
    expect(document.body.textContent).not.toContain("Holds");
  });

  it("shows skeletons, not the empty line, before the first answer arrives", async () => {
    await render({ isLoading: true });

    expect(document.body.textContent).not.toContain("No open positions.");
    expect(rows(".animate-pulse").length).toBeGreaterThan(0);
  });
});

describe("an LP range older than the newest page of executors", () => {
  /** The hook under a probe, exactly as Portfolio and /dex call it. */
  function LpProbe() {
    const { positions, label } = useLpPositions("srv");
    return (
      <PositionsTab
        holds={[]}
        lpPositions={positions}
        lpLabel={label}
        isLoading={false}
        convert={(value) => ({ value, converted: true })}
        formatValue={fmtValue}
        formatPnlValue={fmtPnl}
      />
    );
  }

  it("still appears, because the list is read from a filtered query", async () => {
    // The server churns: the newest page of *all* executors has long since
    // scrolled past the range opened last week.
    UNFILTERED_PAGE = Array.from({ length: 3 }, (_, i) =>
      lpExecutor({ id: `noise-${i}`, type: "position", config: {}, custom_info: {} }),
    );
    LP_EXECUTORS = [lpExecutor({ id: "old-range" })];

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <LpProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();

    expect(rows("[data-lp-row]")).toHaveLength(1);
    expect(document.querySelector("[data-lp-row]")!.textContent).toContain("meteora");

    const { api } = await import("@/lib/api");
    expect(api.getExecutors).toHaveBeenCalledWith("srv", {
      executor_type: "lp_executor",
      limit: 200,
    });
  });

  it("drops a range whose executor has stopped", async () => {
    LP_EXECUTORS = [lpExecutor({ id: "closed", status: "terminated" })];

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <LpProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();

    expect(rows("[data-lp-row]")).toHaveLength(0);
    expect(document.body.textContent).toContain("No open positions.");
  });
});

/**
 * That the pool browser's pager only ever pages the listing on screen (#241).
 *
 * The table keeps the previous page up while the next one loads, and that
 * placeholder used to be carried anywhere — into another tab, and into a query
 * that never runs at all: a ticker typed into Search is not an address, so the
 * search is disabled, and Trending's rows (with Trending's `has_more`) stayed
 * under a pager whose Next moved the counter while the rows never changed.
 *
 * Only the data edges are stubbed — `@/lib/api`, the upstream-budget hook and
 * the LP positions strip above the table; the page's own keying is under test.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { PoolSummary } from "@/lib/api";
import { Dex } from "./Dex";

const getDexPools = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getDexChains: async () => [],
    getDexVenues: async () => [],
    getDexPools: (...args: unknown[]) => getDexPools(...args),
    getDexPoolByAddress: async () => null,
    getDexPoolsByAddress: async () => ({ pools: [] }),
  },
}));

const upstream = {
  limited: false,
  retryIn: 0,
  requestsLastMinute: 0,
  budget: 30,
  report: () => {},
};
vi.mock("@/hooks/useDexUpstream", () => ({ useDexUpstream: () => upstream }));
vi.mock("@/components/dex/LpPositions", () => ({ LpPositions: () => null }));
vi.mock("@/components/dex/UpstreamNotice", () => ({ UpstreamNotice: () => null }));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function pool(base: string, quote: string, dex: string): PoolSummary {
  return {
    address: `${base}${quote}${dex}`,
    name: `${base} / ${quote}`,
    source: "gecko",
    dex_id: dex,
    network: "solana-mainnet-beta",
    gecko_network: "solana",
    gateway_network: "solana-mainnet-beta",
    base_symbol: base,
    quote_symbol: quote,
    base_token_symbol: base,
    quote_token_symbol: quote,
    base_token_address: `${base}mint`,
    quote_token_address: `${quote}mint`,
    trading_pair: `${base}mint-${quote}`,
    lp_provider: null,
    lp_supported: false,
    tradable: true,
    has_bins: false,
    reserve_usd: 1_000_000,
    volume_24h: 500_000,
    price_change_24h: 1,
  };
}

const TRENDING = {
  pools: [pool("SOL", "USDC", "orca"), pool("BONK", "SOL", "raydium")],
  has_more: true,
};

/** A reply the test answers when it chooses to, to observe the page in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

let container: HTMLDivElement;
let root: Root;

async function settle(ms = 0) {
  await act(() => new Promise((r) => setTimeout(r, ms)));
  await act(() => new Promise((r) => setTimeout(r, 0)));
}

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ServerContext.Provider value={{ server: "srv", setServer: () => {} }}>
          <MemoryRouter>
            <Dex />
          </MemoryRouter>
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await settle();
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label,
  );
}

async function click(label: string) {
  const target = button(label);
  expect(target, `no "${label}" button`).toBeDefined();
  await act(async () => target!.click());
  await settle();
}

async function type(value: string) {
  const input = container.querySelector("input[placeholder^='Paste']") as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const text = () => container.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  getDexPools.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(() => root.unmount());
  container.remove();
});

describe("Dex pool pager", () => {
  it("does not leave Trending's rows under Search when the query is a ticker", async () => {
    getDexPools.mockResolvedValue(TRENDING);
    await render();
    expect(text()).toContain("BONK-SOL");
    expect(button("Next")?.disabled).toBe(false);

    await click("Search");
    expect(text()).not.toContain("BONK-SOL");
    expect(text()).toContain("Paste a pool or token address to find it.");

    await type("SOL-USDC");
    await settle(450); // past the search debounce
    expect(text()).not.toContain("BONK-SOL");
    expect(text()).toContain("That is not a pool or token address.");
    expect(button("Next")).toBeUndefined();
    // A ticker never reaches the backend: the only request was Trending's.
    expect(getDexPools).toHaveBeenCalledTimes(1);
  });

  it("does not carry one tab's rows into another while it loads", async () => {
    const top = deferred<typeof TRENDING>();
    getDexPools.mockImplementation((_server: string, params: { view: string }) =>
      params.view === "top" ? top.promise : Promise.resolve(TRENDING),
    );
    await render();

    await click("Top");
    expect(text()).not.toContain("BONK-SOL");
    expect(text()).toContain("Loading pools…");

    top.resolve({ pools: [pool("JUP", "USDC", "meteora")], has_more: false });
    await settle();
    expect(text()).toContain("JUP-USDC");
  });

  it("keeps the current page up while Next loads the one after it", async () => {
    const second = deferred<typeof TRENDING>();
    getDexPools.mockImplementation((_server: string, params: { page: number }) =>
      params.page === 2 ? second.promise : Promise.resolve(TRENDING),
    );
    await render();

    await click("Next");
    expect(getDexPools).toHaveBeenLastCalledWith(
      "srv",
      expect.objectContaining({ view: "trending", page: 2 }),
    );
    expect(text()).toContain("Page 2");
    expect(text()).toContain("BONK-SOL");

    second.resolve({ pools: [pool("JUP", "USDC", "meteora")], has_more: false });
    await settle();
    expect(text()).toContain("JUP-USDC");
    expect(text()).not.toContain("BONK-SOL");
  });
});

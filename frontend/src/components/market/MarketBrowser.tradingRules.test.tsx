/**
 * The browser's tradable-pairs filter is fed by the shared `useTradingRules`
 * hook, not by a second rules query of its own (ARCH-310).
 *
 * The point of the hook is that the trade page and the browser declare the
 * `trading-rules` cache entry — key, staleTime, enabled — in exactly one place.
 * To prove the browser really goes through it, the module mock below hands the
 * hook one pair list while the `@/lib/api` mock hands `getTradingRules` a
 * different one. Whichever list ends up on screen names the declaration the
 * component is actually using.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Ticker, TradingRule } from "@/lib/api";
import { MarketBrowser } from "./MarketBrowser";
import { useTradingRules } from "./useTradingRules";

function rule(trading_pair: string): TradingRule {
  return {
    trading_pair,
    min_order_size: 0,
    min_notional_size: 0,
    min_price_increment: 0,
    min_base_amount_increment: 0,
  };
}

/** What the shared hook reports: the venue lists SOL-USDC and nothing else. */
const HOOK_PAIR = "SOL-USDC";
/** What a second, component-local rules query would have reported instead. */
const OWN_QUERY_PAIR = "OWN-USDT";

vi.mock("@/lib/api", () => ({
  api: {
    getTickers: vi.fn(async () => ({
      connector: "binance",
      tickers: TICKERS,
      updated_at: 1,
    })),
    getTradingRules: vi.fn(async () => ({
      connector: "binance",
      rules: [rule(OWN_QUERY_PAIR)],
    })),
  },
}));

vi.mock("./useTradingRules", () => ({
  useTradingRules: vi.fn(() => ({
    connector: "binance",
    rules: [rule(HOOK_PAIR)],
  })),
}));

function ticker(trading_pair: string): Ticker {
  return {
    trading_pair,
    price: 100,
    base_volume: 1,
    quote_volume: 100,
    usd_volume: 100,
    change_pct: null,
    change_window_s: null,
  };
}

const TICKERS: Ticker[] = [ticker(HOOK_PAIR), ticker(OWN_QUERY_PAIR)];

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MarketBrowser
          server="srv"
          connector="binance"
          pair="BTC-USDT"
          connectors={["binance", "kraken"]}
          credentialed={new Set(["binance"])}
          onPick={() => {}}
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );
  });
  // react-query resolves on a later macrotask than the render that asked.
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function listedPairs(): string[] {
  return [...document.querySelectorAll("[data-market-row]")].map(
    (r) => r.querySelectorAll("button")[1].textContent ?? "",
  );
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the market browser asks for trading rules through the shared hook", () => {
  it("filters the list by the hook's rules, not by a query of its own", async () => {
    await render();

    expect(listedPairs()).toEqual([HOOK_PAIR]);
    expect(listedPairs()).not.toContain(OWN_QUERY_PAIR);
  });

  it("asks the hook for the venue the rail is scoped to", async () => {
    await render();

    expect(useTradingRules).toHaveBeenCalledWith("srv", "binance");
  });
});

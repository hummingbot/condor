/**
 * The market browser's two load-bearing behaviours (FEAT-053).
 *
 * The Δ column is only worth having if it never claims a window it did not
 * measure, so the header label is derived from what the backend actually
 * reported — 24h when it is 24h, the true hours when the history is younger, a
 * bare Δ when there is nothing to compare against. And picking a row has to
 * carry the *browser's* venue back to the trade surface, not the one the
 * surface was already on, or switching exchanges in here would silently pick a
 * pair on the wrong exchange.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Ticker } from "@/lib/api";
import { changeColumnLabel } from "@/lib/marketChange";
import { MarketBrowser, type MarketPick } from "./MarketBrowser";

vi.mock("@/lib/api", () => ({
  api: {
    getTickers: vi.fn(async () => ({
      connector: "binance",
      tickers: TICKERS,
      updated_at: 1,
    })),
    // No rules: every ticker is offered, which keeps the fixture to one place.
    getTradingRules: vi.fn(async () => ({ connector: "binance", rules: [] })),
  },
}));

const HOUR = 3600;

function ticker(over: Partial<Ticker> & { trading_pair: string }): Ticker {
  return {
    price: 100,
    base_volume: 1,
    quote_volume: 100,
    usd_volume: 100,
    change_pct: null,
    change_window_s: null,
    ...over,
  };
}

let TICKERS: Ticker[] = [];

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
const picked: MarketPick[] = [];

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MarketBrowser
          server="srv"
          connectors={["binance", "kucoin"]}
          connector="binance"
          pair="BTC-USDT"
          onPick={(m) => picked.push(m)}
          onClose={() => {}}
        />
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

/** The Δ column header button — the last one in the header row. */
function changeHeader(): HTMLElement {
  const headers = [...document.querySelectorAll("button")].filter((b) =>
    /Δ/.test(b.textContent ?? ""),
  );
  return headers[0] as HTMLElement;
}

function rowCells(pair: string): HTMLElement[] {
  const rows = [...document.querySelectorAll("[data-market-row]")];
  const row = rows.find((r) => r.textContent?.includes(pair));
  if (!row) throw new Error(`no row for ${pair}`);
  return [...row.querySelectorAll("button")] as HTMLElement[];
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  picked.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the Δ column never claims a window it did not measure", () => {
  it("labels a real 24h window as 24h", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT", change_pct: 5.5, change_window_s: 24 * HOUR }),
    ];
    await render();

    expect(changeHeader().textContent).toContain("24h Δ");
    expect(rowCells("BTC-USDT").at(-1)!.textContent).toBe("+5.50%");
  });

  it("labels an hour-old history as 1h, not 24h", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT", change_pct: -1.25, change_window_s: HOUR }),
    ];
    await render();

    expect(changeHeader().textContent).toContain("1h Δ");
    expect(changeHeader().textContent).not.toContain("24h");
    expect(rowCells("BTC-USDT").at(-1)!.textContent).toBe("-1.25%");
  });

  it("renders a dash — never 0.00% — when there is no reference at all", async () => {
    TICKERS = [ticker({ trading_pair: "BTC-USDT" }), ticker({ trading_pair: "ETH-USDT" })];
    await render();

    expect(changeHeader().textContent?.trim()).toBe("Δ");
    for (const pair of ["BTC-USDT", "ETH-USDT"]) {
      expect(rowCells(pair).at(-1)!.textContent).toBe("—");
    }
  });

  it("shows a dash for a pair listed after the reference snapshot", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT", change_pct: 2, change_window_s: 24 * HOUR }),
      ticker({ trading_pair: "NEW-USDT" }),
    ];
    await render();

    // The column is still labelled from the pairs that do have a window.
    expect(changeHeader().textContent).toContain("24h Δ");
    expect(rowCells("NEW-USDT").at(-1)!.textContent).toBe("—");
  });

  it("takes the label from the longest window on screen", () => {
    expect(changeColumnLabel(24 * HOUR)).toBe("24h Δ");
    expect(changeColumnLabel(23.7 * HOUR)).toBe("24h Δ"); // hourly snapshots drift
    expect(changeColumnLabel(40 * HOUR)).toBe("40h Δ"); // a hole after downtime
    expect(changeColumnLabel(20 * 60)).toBe("20m Δ");
    expect(changeColumnLabel(null)).toBe("Δ");
  });
});

describe("picking a row", () => {
  it("carries the pair back to the trade surface", async () => {
    TICKERS = [ticker({ trading_pair: "SOL-USDC", change_pct: 1, change_window_s: HOUR })];
    await render();

    await act(async () => {
      rowCells("SOL-USDC")[1].click();
    });

    expect(picked).toEqual([{ connector: "binance", pair: "SOL-USDC" }]);
  });

  it("carries the venue the browser switched to, not the one it opened on", async () => {
    TICKERS = [ticker({ trading_pair: "SOL-USDC" })];
    await render();

    const venueButton = [...document.querySelectorAll("button")].find((b) =>
      /Binance/.test(b.textContent ?? ""),
    )!;
    await act(async () => venueButton.click());
    const kucoin = [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Kucoin",
    )!;
    await act(async () => kucoin.click());
    await flush(); // the new venue's tickers are a new query

    await act(async () => {
      rowCells("SOL-USDC")[1].click();
    });

    expect(picked).toEqual([{ connector: "kucoin", pair: "SOL-USDC" }]);
  });
});

describe("the footer", () => {
  it("states how many of how many are shown", async () => {
    TICKERS = [ticker({ trading_pair: "BTC-USDT" }), ticker({ trading_pair: "ETH-USDT" })];
    await render();

    expect(container.textContent).toContain("Showing 2 of 2");
  });

  it("filters across the whole venue, not just the rendered page", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT" }),
      ticker({ trading_pair: "ETH-USDT" }),
      ticker({ trading_pair: "SOL-USDC" }),
    ];
    await render();

    const input = container.querySelector("input")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(input, "sol");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("Showing 1 of 1");
    expect(document.querySelectorAll("[data-market-row]").length).toBe(1);
  });
});

describe("starring", () => {
  it("pins a market to the top and survives a remount", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT", usd_volume: 900 }),
      ticker({ trading_pair: "ZZZ-USDT", usd_volume: 1 }),
    ];
    await render();

    // Sorted by volume, ZZZ is last until it is starred.
    const pairOf = () =>
      [...document.querySelectorAll("[data-market-row]")].map(
        (r) => r.textContent?.slice(0, 3),
      );
    expect(pairOf()).toEqual(["BTC", "ZZZ"]);

    await act(async () => {
      rowCells("ZZZ-USDT")[0].click();
    });
    expect(pairOf()).toEqual(["ZZZ", "BTC"]);

    // A reload reads the same localStorage back.
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(pairOf()).toEqual(["ZZZ", "BTC"]);
  });
});

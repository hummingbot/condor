/**
 * The market browser's two load-bearing behaviours (FEAT-053).
 *
 * The Δ column is only worth having if it never claims a window it did not
 * measure, so the header label is derived from what the backend actually
 * reported — 24h when it is 24h, the true hours when the history is younger, a
 * bare Δ when there is nothing to compare against. And picking a row has to
 * carry the venue the list was scoped to back to the trade surface — which,
 * now that the venue rail lives in here, is not necessarily the venue the trade
 * surface handed it. The rail scopes; only a pick commits.
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

const CONNECTORS = ["binance", "hyperliquid_perpetual", "kraken"];

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
          connector="binance"
          pair="BTC-USDT"
          connectors={CONNECTORS}
          credentialed={new Set(["binance"])}
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

/** The quote-asset filter, and a change event React will actually see. */
function selectQuote(value: string) {
  const select = container.querySelector("select") as HTMLSelectElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    "value",
  )!.set!;
  setter.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

/** A venue in the rail, by its display name. */
function venueOption(label: string): HTMLElement {
  const found = [...document.querySelectorAll('[role="option"]')].find(
    (o) => o.textContent === label,
  );
  if (!found) throw new Error(`no venue option ${label}`);
  return found as HTMLElement;
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
  it("carries the pair and the venue it was scoped to back to the trade surface", async () => {
    TICKERS = [ticker({ trading_pair: "SOL-USDC", change_pct: 1, change_window_s: HOUR })];
    await render();

    await act(async () => {
      rowCells("SOL-USDC")[1].click();
    });

    expect(picked).toEqual([{ connector: "binance", pair: "SOL-USDC" }]);
  });

  it("carries the venue the rail is on, not the one the chart is on", async () => {
    TICKERS = [ticker({ trading_pair: "SOL-USDC" })];
    await render();

    await act(async () => venueOption("Hyperliquid Perp").click());
    await flush();
    // The rail alone commits nothing: the chart is still on binance until a row
    // is picked, which is what spares the page a render on a default pair of
    // the new venue that nobody asked for.
    expect(picked).toEqual([]);

    await act(async () => {
      rowCells("SOL-USDC")[1].click();
    });
    expect(picked).toEqual([
      { connector: "hyperliquid_perpetual", pair: "SOL-USDC" },
    ]);
  });

  it("names the browsed venue in the search field, and marks the chart's own", async () => {
    TICKERS = [ticker({ trading_pair: "SOL-USDC" })];
    await render();

    const input = () => container.querySelector("input")!;
    expect(input().placeholder).toBe("Search Binance markets...");

    await act(async () => venueOption("Kraken").click());
    await flush();
    expect(input().placeholder).toBe("Search Kraken markets...");
    expect(input().getAttribute("aria-label")).toBe("Search Kraken markets");

    // Two different truths, both on screen: the rail's selection is what the
    // table lists, `aria-current` is the venue waiting behind the overlay.
    expect(venueOption("Kraken").getAttribute("aria-selected")).toBe("true");
    expect(venueOption("Binance").getAttribute("aria-current")).toBe("true");
    expect(venueOption("Kraken").getAttribute("aria-current")).toBeNull();
  });

  it("highlights the chart's pair only while the chart's venue is the one listed", async () => {
    TICKERS = [ticker({ trading_pair: "BTC-USDT" })];
    await render();
    const pairCell = () => rowCells("BTC-USDT")[1];
    const highlighted = () => /color-primary/.test(pairCell().className);

    expect(highlighted()).toBe(true);
    // The same pair on another venue is another market, not where you are.
    await act(async () => venueOption("Kraken").click());
    await flush();
    expect(highlighted()).toBe(false);
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

describe("the quote filter", () => {
  it("narrows the table to one quote asset", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT" }),
      ticker({ trading_pair: "ETH-USDT" }),
      ticker({ trading_pair: "SOL-USDC" }),
    ];
    await render();
    expect(container.textContent).toContain("Showing 3 of 3");

    await act(async () => selectQuote("USDC"));
    expect(container.textContent).toContain("Showing 1 of 1");
    expect(rowCells("SOL-USDC")).toBeTruthy();
  });

  it("offers every quote the venue has, whichever one is picked", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT" }),
      ticker({ trading_pair: "SOL-USDC" }),
      ticker({ trading_pair: "ETH-BTC" }),
    ];
    await render();
    const options = () =>
      [...container.querySelectorAll("option")].map((o) => o.textContent);
    // Priority order, not alphabetical: the quotes a trader looks for first.
    expect(options()).toEqual(["All quotes", "USDT", "USDC", "BTC"]);

    // The list is derived from the venue, not from the filtered rows, so
    // filtering never removes the other quotes from the control that filtered.
    await act(async () => selectQuote("BTC"));
    expect(options()).toEqual(["All quotes", "USDT", "USDC", "BTC"]);
  });

  it("resets on a venue switch, so no filter outlives the list it was for", async () => {
    TICKERS = [
      ticker({ trading_pair: "BTC-USDT" }),
      ticker({ trading_pair: "SOL-USDC" }),
    ];
    await render();

    await act(async () => selectQuote("USDC"));
    expect(container.textContent).toContain("Showing 1 of 1");

    // A quote that exists here may not exist on the venue you moved to; a
    // filter that survived the move would show an empty table and no reason why.
    await act(async () => venueOption("Kraken").click());
    await flush();
    expect((container.querySelector("select") as HTMLSelectElement).value).toBe("");
    expect(container.textContent).toContain("Showing 2 of 2");
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

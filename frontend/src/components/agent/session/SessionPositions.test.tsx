/**
 * The positions card `SessionExecutors` draws above its charts, extracted into
 * its own component by ARCH-405.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PositionHeld } from "@/lib/api";

vi.mock("@/components/executor/PairLabel", () => ({
  PairLabel: ({ tradingPair }: { tradingPair: string }) => <span>{tradingPair}</span>,
}));

import { formatPositionPrice, quoteOf } from "./positionFormat";
import { SessionPositions } from "./SessionPositions";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

// Formatters that tag their quote, so a cell shows which currency it was handed.
const formatPnl = vi.fn((val: number, quote: string) => `pnl(${val} ${quote})`);
const formatPrice = vi.fn((val: number, quote: string) => `px(${val} ${quote})`);

function render(positions: PositionHeld[]) {
  act(() => {
    root.render(<SessionPositions positions={positions} formatPnl={formatPnl} formatPrice={formatPrice} />);
  });
}

describe("SessionPositions", () => {
  it("renders nothing when the run holds no positions", () => {
    render([]);
    expect(container.innerHTML).toBe("");
  });

  it("draws one row per position, preferring the quote PnL and breakeven fields", () => {
    render([
      {
        connector_name: "binance_perpetual",
        trading_pair: "SOL-USDT",
        position_side: "LONG",
        net_amount_base: -1.5,
        buy_breakeven_price: 150,
        entry_price: 1,
        current_price: 155.5,
        unrealized_pnl_quote: 8.25,
        unrealized_pnl: 99,
        leverage: 5,
      },
      { connector_name: "okx", trading_pair: "BTC-USDT", side: "SELL" },
    ]);

    expect(container.querySelector("h3")?.textContent).toBe("Positions Held (2)");
    const rows = Array.from(container.querySelectorAll("tbody tr")).map((tr) =>
      Array.from(tr.querySelectorAll("td")).map((td) => td.textContent),
    );
    expect(rows[0]).toEqual(["SOL-USDT", "LONG", "1.5000", "px(150 USDT)", "px(155.5 USDT)", "pnl(8.25 USDT)", "5x"]);
    expect(rows[1]).toEqual(["BTC-USDT", "SELL", "0.0000", "px(0 USDT)", "px(0 USDT)", "pnl(0 USDT)", "—"]);
  });

  it("hands a BRL-quoted position's prices and PnL to the formatters in BRL, never under a bare $", () => {
    render([
      {
        connector_name: "binance",
        trading_pair: "BTC-BRL",
        side: "BUY",
        amount: 0.01,
        buy_breakeven_price: 312000,
        current_price: 318000,
        unrealized_pnl_quote: 60,
      },
    ]);

    expect(formatPrice).toHaveBeenCalledWith(312000, "BRL");
    expect(formatPrice).toHaveBeenCalledWith(318000, "BRL");
    expect(formatPnl).toHaveBeenCalledWith(60, "BRL");
    const cells = Array.from(container.querySelectorAll("tbody td")).map((td) => td.textContent ?? "");
    expect(cells.some((c) => c.startsWith("$"))).toBe(false);
  });
});

describe("formatPositionPrice", () => {
  it("keeps a price at full precision instead of abbreviating it to K", () => {
    expect(formatPositionPrice(57412.5, "R$")).toBe("R$57412.50");
    expect(formatPositionPrice(150)).toBe("$150.00");
    expect(formatPositionPrice(0)).toBe("$0.00");
  });

  it("keeps significant digits for a sub-unit price", () => {
    expect(formatPositionPrice(0.00001234, "₿")).toBe("₿0.00001234");
  });
});

describe("quoteOf", () => {
  it("reads the quote of a pair and falls back to USDT", () => {
    expect(quoteOf("BTC-BRL")).toBe("BRL");
    expect(quoteOf("SOL")).toBe("USDT");
    expect(quoteOf(undefined)).toBe("USDT");
  });
});

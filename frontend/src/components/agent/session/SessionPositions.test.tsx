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

function render(positions: PositionHeld[]) {
  act(() => {
    root.render(<SessionPositions positions={positions} />);
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
    expect(rows[0].slice(0, 5)).toEqual(["SOL-USDT", "LONG", "1.5000", "$150.00", "$155.50"]);
    expect(rows[0][6]).toBe("5x");
    expect(rows[1].slice(0, 5)).toEqual(["BTC-USDT", "SELL", "0.0000", "$0.00", "$0.00"]);
    expect(rows[1][6]).toBe("—");
  });
});

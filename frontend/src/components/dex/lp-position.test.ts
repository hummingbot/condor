/**
 * What an LP position's money figures are *denominated in*, and how they render.
 *
 * The regression this pins: `total_value_quote` / `fees_earned_quote` /
 * `net_pnl_quote` are all measured in the pair's quote — SOL on an `X-SOL` range —
 * and the cards used to prefix every one of them with a `$`. A 0.163 SOL range
 * worth about $20 read as "$0.16": not a rounding error but a wrong number, off
 * by the whole SOL/USD rate. The fix prices the figure off the pool's own quote
 * token, and when that price is unknown leaves it in SOL under the SOL label.
 */

import { describe, expect, it } from "vitest";

import type { ExecutorInfo } from "@/lib/api";

import { lpFees, lpPnl, lpValue, positionQuote, readLpPosition } from "./lp-position";

/** The SOL price used across these cases, so the dollars are checkable by hand. */
const SOL = 121.69;

function lpExecutor(over: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "e1",
    type: "lp_executor",
    connector: "solana-mainnet-beta",
    trading_pair: "MINT-SOL",
    side: "",
    status: "running",
    close_type: "",
    pnl: 0,
    volume: 0,
    timestamp: 0,
    controller_id: "",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 0,
    current_price: 0,
    close_timestamp: 0,
    custom_info: {},
    config: { pool_address: "POOL", connector_name: "solana-mainnet-beta" },
    ...over,
  } as unknown as ExecutorInfo;
}

describe("positionQuote", () => {
  it("reads the quote off the pair and uppercases it", () => {
    expect(positionQuote("oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp-SOL")).toBe("SOL");
    expect(positionQuote("So11111111111111111111111111111111111111112-USDC")).toBe("USDC");
  });

  it("falls back to USDT for a pair with no quote segment", () => {
    expect(positionQuote("")).toBe("USDT");
    expect(positionQuote("SOLONLY")).toBe("USDT");
  });
});

describe("readLpPosition", () => {
  it("carries the quote the money figures are denominated in", () => {
    const pos = readLpPosition(lpExecutor({ trading_pair: "MINT-SOL" }));
    expect(pos?.quote).toBe("SOL");
  });
});

describe("LP money in USD when the quote's price is known", () => {
  it("prices the position value, PnL and fees in dollars", () => {
    expect(lpValue(0.163339, SOL, "SOL")).toBe("$19.88");
    expect(lpPnl(0.003097, SOL, "SOL")).toBe("+$0.38");
    expect(lpFees(0.00136, SOL, "SOL")).toBe("$0.17");
  });
});

describe("LP money with no rate: quote units, not dollars", () => {
  it("keeps the figure in SOL and labels it SOL", () => {
    expect(lpValue(0.163339, null, "SOL")).toBe("0.1633 SOL");
    expect(lpPnl(-0.00025288, null, "SOL")).toBe("-0.0002529 SOL");
  });

  it("never stamps a dollar on an unconverted figure", () => {
    expect(lpValue(0.163339, null, "SOL")).not.toContain("$");
    expect(lpPnl(0.003, null, "SOL")).not.toContain("$");
    expect(lpFees(2.4e-5, null, "SOL")).not.toContain("$");
  });
});

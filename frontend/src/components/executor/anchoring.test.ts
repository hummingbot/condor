/**
 * Every panel opens with its lines already on the chart.
 *
 * A price line the user has to conjure before they can drag it is a line that
 * does not exist, so each panel offers the market's price to its empty prices
 * the first time one arrives. The subtle half is that the offer is *spent*: the
 * live price ticks every second, and an anchoring that ran on each tick would
 * refill a field the moment the user cleared it — a position entry set back to
 * 0 means "market order" and has to stay 0. These cases pin that boundary for
 * all four reducers, since it is one rule with four implementations.
 */

import { describe, expect, it } from "vitest";

import { GRID_DEFAULTS, autoFillGridPrices, gridReducer } from "@/lib/gridExecutor";
import { DCA_DEFAULTS, dcaReducer } from "./dca-config";
import { ORDER_DEFAULTS, orderReducer } from "./order-config";
import { POSITION_DEFAULTS, positionReducer } from "./position-config";

const PRICE = 100;

describe("grid anchoring", () => {
  it("draws a range around the first price it sees", () => {
    const state = gridReducer(GRID_DEFAULTS, { type: "ANCHOR", price: PRICE });
    expect(state).toMatchObject({ ...autoFillGridPrices(1, PRICE), anchored: true });
  });

  it("leans the range the way the side does", () => {
    const long = gridReducer(GRID_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const short = gridReducer(
      { ...GRID_DEFAULTS, side: 2 },
      { type: "ANCHOR", price: PRICE },
    );
    // A long grid keeps its stop below the range, a short above it.
    expect(long.limit_price).toBeLessThan(long.start_price);
    expect(short.limit_price).toBeGreaterThan(short.end_price);
  });

  it("leaves a range the user has started drawing alone", () => {
    const started = { ...GRID_DEFAULTS, start_price: 42 };
    const state = gridReducer(started, { type: "ANCHOR", price: PRICE });
    expect(state.start_price).toBe(42);
    expect(state.end_price).toBe(0);
    // Still spent: the user is placing these, and the next tick must not help.
    expect(state.anchored).toBe(true);
  });

  it("ignores every tick after the first", () => {
    const anchored = gridReducer(GRID_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const cleared = gridReducer(anchored, { type: "SET_FIELD", field: "start_price", value: 0 });
    expect(gridReducer(cleared, { type: "ANCHOR", price: 200 })).toBe(cleared);
  });

  it("anchors again on the next market", () => {
    const anchored = gridReducer(GRID_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const moved = gridReducer(anchored, { type: "SET_PAIR", value: "ETH-USDT" });
    expect(moved.anchored).toBe(false);
    expect(moved.start_price).toBe(0);
    expect(gridReducer(moved, { type: "ANCHOR", price: 200 }).start_price).toBeGreaterThan(0);
  });
});

describe("position anchoring", () => {
  it("fills the entry, which is what the exits hang off", () => {
    const state = positionReducer(POSITION_DEFAULTS, { type: "ANCHOR", price: PRICE });
    expect(state.entry_price).toBe(PRICE);
    expect(state.anchored).toBe(true);
  });

  it("keeps an entry cleared back to a market order", () => {
    const anchored = positionReducer(POSITION_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const market = positionReducer(anchored, { type: "SET_FIELD", field: "entry_price", value: 0 });
    expect(positionReducer(market, { type: "ANCHOR", price: 200 }).entry_price).toBe(0);
  });

  it("re-anchors on a new connector", () => {
    const anchored = positionReducer(POSITION_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const moved = positionReducer(anchored, { type: "SET_CONNECTOR", value: "kucoin" });
    expect(moved).toMatchObject({ entry_price: 0, anchored: false });
  });
});

describe("order anchoring", () => {
  it("fills the price whatever the strategy is", () => {
    // The field is hidden on a market order, but switching to LIMIT afterwards
    // should find a price already there rather than an empty one.
    const market = { ...ORDER_DEFAULTS, execution_strategy: "MARKET" };
    expect(orderReducer(market, { type: "ANCHOR", price: PRICE }).price).toBe(PRICE);
  });

  it("leaves a price the user typed alone", () => {
    const typed = { ...ORDER_DEFAULTS, price: 42 };
    expect(orderReducer(typed, { type: "ANCHOR", price: PRICE }).price).toBe(42);
  });
});

describe("dca anchoring", () => {
  it("ladders every level away from the price", () => {
    const state = dcaReducer(DCA_DEFAULTS, { type: "ANCHOR", price: PRICE });
    // BUY buys cheaper on the way down.
    expect(state.prices).toEqual([98, 96, 94]);
    expect(state.anchored).toBe(true);
  });

  it("ladders upwards to sell", () => {
    const sell = { ...DCA_DEFAULTS, side: 2 as const };
    expect(dcaReducer(sell, { type: "ANCHOR", price: PRICE }).prices).toEqual([102, 104, 106]);
  });

  it("leaves a ladder the user has started placing alone", () => {
    const started = { ...DCA_DEFAULTS, prices: [95, 0, 0] };
    expect(dcaReducer(started, { type: "ANCHOR", price: PRICE }).prices).toEqual([95, 0, 0]);
  });

  it("continues the ladder when a level is added", () => {
    const anchored = dcaReducer(DCA_DEFAULTS, { type: "ANCHOR", price: PRICE });
    const grown = dcaReducer(anchored, { type: "ADD_LEVEL" });
    // A new rung arrives as a line to drag, not as an empty field.
    expect(grown.prices).toHaveLength(4);
    expect(grown.prices[3]).toBeLessThan(grown.prices[2]);
  });

  it("adds an empty rung to a ladder that has no prices yet", () => {
    const empty = { ...DCA_DEFAULTS, prices: [0, 0, 0] };
    expect(dcaReducer(empty, { type: "ADD_LEVEL" }).prices).toEqual([0, 0, 0, 0]);
  });
});

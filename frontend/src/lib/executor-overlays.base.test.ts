/**
 * @vitest-environment jsdom
 *
 * The fields every executor overlay shares (ARCH-332).
 *
 * The five `compute*Overlay` builders used to write out the same ten-field tail
 * by hand, so a field added to `ExecutorOverlay` had to be added five times and
 * an optional one missed in a builder drifted silently -- no type error, just a
 * chart that lost a value for one executor type (the shape CORR-280 shipped
 * once, with `side` read differently per builder). They now all return through
 * `baseOverlay`, and this pins that: one executor of every type, each carrying
 * distinct values, with every shared field asserted against the source
 * `ExecutorInfo`. A builder that goes back to hand-copying and drops or crosses
 * a field fails here.
 */

import { describe, expect, it } from "vitest";

import type { ExecutorInfo } from "./api";
import { computeMultiOverlays } from "./executor-overlays";

function executor(patch: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "e1",
    type: "position",
    connector: "binance_perpetual",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "running",
    close_type: "",
    pnl: 5,
    volume: 100,
    timestamp: 1_700_000_000,
    controller_id: "c1",
    cum_fees_quote: 0.1,
    net_pnl_pct: 0.01,
    entry_price: 200,
    current_price: 210,
    close_timestamp: 0,
    custom_info: {},
    config: {},
    ...patch,
  };
}

/** One closed executor per builder, each field distinct so a swap is visible. */
const cases: Array<{ label: string; overlayType: string; executor: ExecutorInfo }> = [
  {
    label: "position",
    overlayType: "position",
    executor: executor({
      id: "pos-1",
      type: "position",
      side: "BUY",
      status: "terminated",
      close_type: "TAKE_PROFIT",
      pnl: 11.5,
      net_pnl_pct: 0.021,
      volume: 1101,
      cum_fees_quote: 1.11,
      timestamp: 1_700_000_100,
      close_timestamp: 1_700_003_100,
      config: { stop_loss: 0.02, take_profit: 0.04 },
    }),
  },
  {
    label: "grid",
    overlayType: "grid",
    executor: executor({
      id: "grid-2",
      type: "grid",
      side: "SELL",
      status: "terminated",
      close_type: "EARLY_STOP",
      pnl: -22.5,
      net_pnl_pct: -0.032,
      volume: 2202,
      cum_fees_quote: 2.22,
      timestamp: 1_700_000_200,
      close_timestamp: 1_700_003_200,
      config: { start_price: 190, end_price: 210, limit_price: 180 },
    }),
  },
  {
    label: "lp",
    overlayType: "lp",
    executor: executor({
      id: "lp-3",
      type: "lp",
      side: "",
      status: "terminated",
      close_type: "TIME_LIMIT",
      pnl: 33.5,
      net_pnl_pct: 0.043,
      volume: 3303,
      cum_fees_quote: 3.33,
      timestamp: 1_700_000_300,
      close_timestamp: 1_700_003_300,
      custom_info: { lower_price: 180, upper_price: 220 },
      config: { lower_price: 181, upper_price: 219 },
    }),
  },
  {
    label: "order",
    overlayType: "order",
    executor: executor({
      id: "order-4",
      type: "order",
      side: "BUY",
      status: "terminated",
      close_type: "FAILED",
      pnl: -4.5,
      net_pnl_pct: -0.054,
      volume: 4404,
      cum_fees_quote: 4.44,
      timestamp: 1_700_000_400,
      close_timestamp: 1_700_003_400,
      config: { price: 205, amount: 2, execution_strategy: "LIMIT_CHASER" },
    }),
  },
  {
    label: "generic fallback",
    overlayType: "dca",
    executor: executor({
      id: "dca-5",
      type: "DCA",
      side: "SELL",
      status: "terminated",
      close_type: "STOP_LOSS",
      pnl: 55.5,
      net_pnl_pct: 0.065,
      volume: 5505,
      cum_fees_quote: 5.55,
      timestamp: 1_700_000_500,
      close_timestamp: 1_700_003_500,
      config: { dca_levels: 3 },
    }),
  },
];

describe("every overlay builder copies the same shared fields", () => {
  it("covers all five builders", () => {
    // computeExecutorOverlay switches on four named types plus the fallback.
    expect(new Set(cases.map((c) => c.overlayType)).size).toBe(5);
  });

  it.each(cases)("$label carries the executor's own values through", ({ overlayType, executor: ex }) => {
    const overlay = computeMultiOverlays([ex])[0];

    expect(overlay.type).toBe(overlayType);
    expect(overlay.executorId).toBe(ex.id);
    expect(overlay.side).toBe(ex.side === "BUY" ? "buy" : "sell");
    expect(overlay.status).toBe(ex.status);
    expect(overlay.closeType).toBe(ex.close_type);
    expect(overlay.pnl).toBe(ex.pnl);
    expect(overlay.pnlPct).toBe(ex.net_pnl_pct);
    expect(overlay.volume).toBe(ex.volume);
    expect(overlay.fees).toBe(ex.cum_fees_quote);
    expect(overlay.config).toBe(ex.config);
    expect(overlay.timeRange).toEqual({ start: ex.timestamp, end: ex.close_timestamp });
  });
});

describe("the lifetime range", () => {
  it("clamps a still-open executor's end to now", () => {
    const now = Math.floor(Date.now() / 1000);
    const overlay = computeMultiOverlays([executor({ close_timestamp: 0 })])[0];

    expect(overlay.timeRange.start).toBe(1_700_000_000);
    expect(overlay.timeRange.end).toBeGreaterThanOrEqual(now);
    expect(overlay.timeRange.end).toBeLessThanOrEqual(now + 2);
  });

  it("clamps a timestamp-less executor's start to now rather than the epoch", () => {
    const now = Math.floor(Date.now() / 1000);
    const overlay = computeMultiOverlays([executor({ timestamp: 0, close_timestamp: 0 })])[0];

    expect(overlay.timeRange.start).toBeGreaterThanOrEqual(now);
    expect(overlay.timeRange.start).toBeLessThanOrEqual(now + 2);
  });
});

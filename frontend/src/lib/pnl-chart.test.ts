/**
 * The PNL chart fold, pinned (ARCH-243).
 *
 * `aggregatePnlSeries` is what turns stored controller snapshots into the line
 * the dashboard draws, and its subtle part is the forward-fill: at any point on
 * the merged timeline every enabled controller must contribute its latest value
 * at or before that instant, or the portfolio total visibly dips every time one
 * controller happens not to have a snapshot at that second. These tests pin the
 * fold's observed behaviour before the windowing/pagination work changes it.
 *
 * The live "now" point uses `Date.now()`, so the suites that care about it
 * freeze the clock.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "./api";
import { controllerKey } from "./controller-identity";
import type { ClosedOutcome } from "./pnl-chart";
import {
  HISTORY_POINT_BUDGET,
  POSITION_AXIS_PAD,
  SAMPLING_INTERVALS,
  VOLUME_BAR_DUTY,
  VOLUME_BAR_MAX_PX,
  VOLUME_BAR_MIN_PX,
  aggregatePnlSeries,
  chartBucketMs,
  executorSeries,
  formatBucketLabel,
  pickSamplingInterval,
  positionAreaExtent,
  resolveTimeRange,
  sliceToRange,
  positionAxisDomain,
  positionQuoteValue,
  samplingIntervalSince,
  snapshotsFromRunHistory,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
} from "./pnl-chart";

const NOW = Date.parse("2026-08-27T12:00:00Z");

/** A snapshot with every field the fold reads; `over` supplies what a case cares about. */
function snap(over: Partial<ControllerPerformanceSnapshot> = {}): ControllerPerformanceSnapshot {
  return {
    timestamp: "2026-08-27T10:00:00Z",
    bot_name: "bot",
    controller_id: "ctrl-a",
    controller_name: "ctrl-a",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
    ...over,
  };
}

/** A live controller, the source of the appended "now" point. */
function ctrl(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "ctrl-a",
    controller_type: "",
    controller_id: "ctrl-a",
    bot_name: "bot",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: null,
    config: {},
    ...over,
  };
}

/**
 * The fold's identity key. A controller is identified by its bot *and* its
 * config id (CORR-241), so `enabledIds` holds composites — the fixtures above
 * all belong to bot "bot" unless a case says otherwise.
 */
const key = (cid: string, bot = "bot") => controllerKey({ bot_name: bot, controller_id: cid });

const at = (hhmm: string) => `2026-08-27T${hhmm}:00Z`;
const ms = (hhmm: string) => Date.parse(at(hhmm));

afterEach(() => {
  vi.useRealTimers();
});

describe("aggregatePnlSeries — degenerate inputs", () => {
  it("returns nothing for no snapshots, even when live controllers are enabled", () => {
    // The live "now" point rides on the snapshot timeline: no history, no chart.
    expect(aggregatePnlSeries([], new Set([key("ctrl-a")]), [ctrl()])).toEqual([]);
  });

  it("returns nothing when every snapshot belongs to a disabled controller", () => {
    const points = aggregatePnlSeries([snap()], new Set([key("ctrl-b")]), []);
    expect(points).toEqual([]);
  });

  it("drops snapshots whose controller has no id at all", () => {
    const orphan = snap({ controller_id: "", controller_name: "" });
    expect(aggregatePnlSeries([orphan], new Set([key("ctrl-a")]), [])).toEqual([]);
  });

  it("turns a single snapshot into a single point", () => {
    const points = aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), realized_pnl_quote: 7, unrealized_pnl_quote: 3, volume_traded: 100 })],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points).toEqual([
      // A lone snapshot is an opening reading, so it carries no bucket of
      // its own: nothing to have been a difference from.
      { time: ms("10:00"), realized: 7, unrealized: 3, total: 10, volume: 100, volumeDelta: 0, position: 0 },
    ]);
  });

  it("falls back to controller_name when controller_id is empty", () => {
    const named = snap({ controller_id: "", controller_name: "by-name", realized_pnl_quote: 5 });
    const points = aggregatePnlSeries([named], new Set([key("by-name")]), []);
    expect(points).toHaveLength(1);
    expect(points[0].realized).toBe(5);
  });
});

describe("aggregatePnlSeries — the merged timeline", () => {
  it("sorts unsorted input into a strictly increasing timeline", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:20"), realized_pnl_quote: 3 }),
        snap({ timestamp: at("10:00"), realized_pnl_quote: 1 }),
        snap({ timestamp: at("10:10"), realized_pnl_quote: 2 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points.map((p) => p.time)).toEqual([ms("10:00"), ms("10:10"), ms("10:20")]);
    expect(points.map((p) => p.realized)).toEqual([1, 2, 3]);
  });

  it("collapses duplicate timestamps into one point, keeping the last snapshot at that instant", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), realized_pnl_quote: 1 }),
        snap({ timestamp: at("10:00"), realized_pnl_quote: 9 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points).toHaveLength(1);
    expect(points[0].realized).toBe(9);
  });

  it("accepts epoch seconds and epoch millis alongside ISO strings", () => {
    const seconds = ms("10:00") / 1000;
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: ms("10:10") as unknown as string, realized_pnl_quote: 2 }),
        snap({ timestamp: seconds as unknown as string, realized_pnl_quote: 1 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points.map((p) => p.time)).toEqual([ms("10:00"), ms("10:10")]);
  });
});

describe("aggregatePnlSeries — folding several controllers into one series", () => {
  it("sums the enabled controllers at every point on the merged timeline", () => {
    const points = aggregatePnlSeries(
      [
        snap({ controller_id: "a", timestamp: at("10:00"), realized_pnl_quote: 1, volume_traded: 10 }),
        snap({ controller_id: "b", timestamp: at("10:05"), realized_pnl_quote: 4, volume_traded: 40 }),
        snap({ controller_id: "a", timestamp: at("10:10"), realized_pnl_quote: 2, volume_traded: 20 }),
      ],
      new Set([key("a"), key("b")]),
      [],
    );
    expect(points.map((p) => [p.time, p.realized, p.volume])).toEqual([
      [ms("10:00"), 1, 10], // only a has reported
      [ms("10:05"), 5, 50], // a forward-filled at 1/10, plus b
      [ms("10:10"), 6, 60], // a moves to 2/20, b forward-filled at 4/40
    ]);
  });

  it("forward-fills a sparse controller at every later timestamp", () => {
    const points = aggregatePnlSeries(
      [
        snap({ controller_id: "sparse", timestamp: at("10:00"), realized_pnl_quote: 100 }),
        snap({ controller_id: "busy", timestamp: at("10:01"), realized_pnl_quote: 1 }),
        snap({ controller_id: "busy", timestamp: at("10:02"), realized_pnl_quote: 2 }),
        snap({ controller_id: "busy", timestamp: at("10:03"), realized_pnl_quote: 3 }),
      ],
      new Set([key("sparse"), key("busy")]),
      [],
    );
    // `sparse` stopped reporting after 10:00 but still counts for 100 throughout.
    expect(points.map((p) => p.realized)).toEqual([100, 101, 102, 103]);
  });

  it("contributes nothing for a controller before its first snapshot", () => {
    const points = aggregatePnlSeries(
      [
        snap({ controller_id: "early", timestamp: at("10:00"), realized_pnl_quote: 10 }),
        snap({ controller_id: "late", timestamp: at("10:10"), realized_pnl_quote: 5 }),
      ],
      new Set([key("early"), key("late")]),
      [],
    );
    expect(points.map((p) => p.realized)).toEqual([10, 15]);
  });

  it("excludes a disabled controller from every point, not just from the last one", () => {
    const snapshots = [
      snap({ controller_id: "keep", timestamp: at("10:00"), realized_pnl_quote: 1 }),
      snap({ controller_id: "drop", timestamp: at("10:05"), realized_pnl_quote: 1000 }),
      snap({ controller_id: "keep", timestamp: at("10:10"), realized_pnl_quote: 2 }),
    ];
    const points = aggregatePnlSeries(snapshots, new Set([key("keep")]), []);
    // 10:05 was only `drop`'s timestamp, so it is not even on the timeline.
    expect(points.map((p) => [p.time, p.realized])).toEqual([
      [ms("10:00"), 1],
      [ms("10:10"), 2],
    ]);
  });

  it("keeps total as realized + unrealized at every point", () => {
    const points = aggregatePnlSeries(
      [
        snap({ controller_id: "a", timestamp: at("10:00"), realized_pnl_quote: 3, unrealized_pnl_quote: -1 }),
        snap({ controller_id: "b", timestamp: at("10:00"), realized_pnl_quote: 2, unrealized_pnl_quote: 4 }),
      ],
      new Set([key("a"), key("b")]),
      [],
    );
    expect(points[0]).toMatchObject({ realized: 5, unrealized: 3, total: 8 });
  });
});

describe("aggregatePnlSeries — positions", () => {
  it("folds positions_summary into a signed quote value per point", () => {
    const points = aggregatePnlSeries(
      [
        snap({
          timestamp: at("10:00"),
          positions_summary: [
            { amount: 2, breakeven_price: 50, side: "BUY" },
            { amount: 1, breakeven_price: 10, side: "SELL" },
          ],
        }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points[0].position).toBe(90); // +100 long, -10 short
  });

  it("ignores a positions_summary that is not an array", () => {
    const points = aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), positions_summary: null as unknown as Record<string, unknown>[] })],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points[0].position).toBe(0);
  });
});

describe("aggregatePnlSeries — currency conversion", () => {
  const convert = vi.fn((value: number, quote: string) => ({
    value: quote === "USDC" ? value * 2 : value,
    converted: quote === "USDC",
  }));

  it("converts every series with the quote of the snapshot's own pair", () => {
    convert.mockClear();
    const points = aggregatePnlSeries(
      [
        snap({
          timestamp: at("10:00"),
          trading_pair: "SOL-USDC",
          realized_pnl_quote: 1,
          unrealized_pnl_quote: 2,
          volume_traded: 3,
          positions_summary: [{ amount: 1, breakeven_price: 4, side: "BUY" }],
        }),
      ],
      new Set([key("ctrl-a")]),
      [],
      convert,
    );
    expect(points[0]).toMatchObject({ realized: 2, unrealized: 4, total: 6, volume: 6, position: 8 });
    expect(convert).toHaveBeenCalledWith(expect.any(Number), "USDC");
  });

  it("falls back to the live controller's pair when the snapshot carries none", () => {
    convert.mockClear();
    aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), trading_pair: "", realized_pnl_quote: 1 })],
      new Set([key("ctrl-a")]),
      [ctrl({ trading_pair: "SOL-USDC" })],
      convert,
    );
    expect(convert).toHaveBeenCalledWith(1, "USDC");
  });

  it("assumes USDT when neither the snapshot nor a controller names a pair", () => {
    convert.mockClear();
    aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), trading_pair: "", realized_pnl_quote: 1 })],
      new Set([key("ctrl-a")]),
      [],
      convert,
    );
    expect(convert).toHaveBeenCalledWith(1, "USDT");
  });

  it("leaves values untouched when no convert function is given", () => {
    const points = aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), realized_pnl_quote: 1.5, volume_traded: 9 })],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(points[0]).toMatchObject({ realized: 1.5, volume: 9 });
  });
});

describe("aggregatePnlSeries — the live \"now\" point", () => {
  it("appends one point at the current time from the enabled live controllers", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [snap({ timestamp: at("10:00"), realized_pnl_quote: 1 })],
      new Set([key("ctrl-a")]),
      [ctrl({ realized_pnl_quote: 11, unrealized_pnl_quote: 2, volume_traded: 500 })],
    );
    expect(points).toHaveLength(2);
    expect(points[1]).toEqual({
      time: NOW,
      realized: 11,
      unrealized: 2,
      total: 13,
      volume: 500,
      // The live point closes an in-progress bucket: everything the counter has
      // moved since the last stored snapshot, which reported nothing traded.
      volumeDelta: 500,
      position: 0,
    });
  });

  it("sums the live point over the enabled controllers only", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [snap({ controller_id: "a", timestamp: at("10:00") })],
      new Set([key("a")]),
      [ctrl({ controller_id: "a", realized_pnl_quote: 7 }), ctrl({ controller_id: "b", realized_pnl_quote: 99 })],
    );
    expect(points[points.length - 1]).toMatchObject({ time: NOW, realized: 7 });
  });

  it("appends no live point when no live controller is enabled", () => {
    const points = aggregatePnlSeries(
      [snap({ controller_id: "a", timestamp: at("10:00") })],
      new Set([key("a")]),
      [ctrl({ controller_id: "b", realized_pnl_quote: 99 })],
    );
    expect(points).toHaveLength(1);
  });

  it("takes the live point from the controller alone, not forward-filled from snapshots", () => {
    // The live point is a fresh read: a controller with history but a live
    // reading of zero lands at zero, it does not inherit its last snapshot.
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [snap({ controller_id: "a", timestamp: at("10:00"), realized_pnl_quote: 42 })],
      new Set([key("a")]),
      [ctrl({ controller_id: "a", realized_pnl_quote: 0 })],
    );
    expect(points.map((p) => p.realized)).toEqual([42, 0]);
  });
});

/**
 * Two bots deployed from one controller config (CORR-241).
 *
 * `controller_id` is the config id, so both bots' rows arrive under the same
 * one. Folded on that id alone they became a single series whose forward-fill
 * alternated between the two bots' values instead of summing them — while the
 * live "now" point, which iterates the controllers, summed them anyway and put
 * a step at the right edge of the chart.
 */
describe("aggregatePnlSeries — two bots sharing a controller config id", () => {
  const shared = "grid_sol";
  const alpha = (over: Partial<ControllerPerformanceSnapshot> = {}) =>
    snap({ bot_name: "alpha", controller_id: shared, controller_name: shared, ...over });
  const beta = (over: Partial<ControllerPerformanceSnapshot> = {}) =>
    snap({ bot_name: "beta", controller_id: shared, controller_name: shared, ...over });

  it("keeps them apart, so a shared timestamp sums instead of overwriting", () => {
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), realized_pnl_quote: 10, volume_traded: 100 }),
        beta({ timestamp: at("10:00"), realized_pnl_quote: 3, volume_traded: 30 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [],
    );

    expect(points).toHaveLength(1);
    expect(points[0]).toMatchObject({ realized: 13, volume: 130 });
  });

  it("forward-fills each bot on its own, rather than alternating between them", () => {
    // alpha reports at 10:00 and 11:00, beta only at 10:30. At 11:00 the total
    // must still carry beta's last value; keyed on the bare id the two bots
    // shared one cursor and 11:00 read as alpha's 5 alone.
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), realized_pnl_quote: 1 }),
        beta({ timestamp: at("10:30"), realized_pnl_quote: 100 }),
        alpha({ timestamp: at("11:00"), realized_pnl_quote: 5 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [],
    );

    expect(points.map((p) => p.realized)).toEqual([1, 101, 105]);
  });

  it("folds only the bot whose chip is enabled", () => {
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), realized_pnl_quote: 10 }),
        beta({ timestamp: at("10:00"), realized_pnl_quote: 3 }),
      ],
      new Set([key(shared, "beta")]),
      [],
    );

    expect(points.map((p) => p.realized)).toEqual([3]);
  });

  it("ends flush with the live point instead of stepping up at the right edge", () => {
    // Acceptance: the last historical total and the live "now" total agree when
    // the live controllers read what they last dumped. The live point always
    // summed both bots; only the history half was collapsing into one series.
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), realized_pnl_quote: 10, unrealized_pnl_quote: 1 }),
        beta({ timestamp: at("10:00"), realized_pnl_quote: 3, unrealized_pnl_quote: 2 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [
        ctrl({ bot_name: "alpha", controller_id: shared, realized_pnl_quote: 10, unrealized_pnl_quote: 1 }),
        ctrl({ bot_name: "beta", controller_id: shared, realized_pnl_quote: 3, unrealized_pnl_quote: 2 }),
      ],
    );

    expect(points).toHaveLength(2);
    expect(points[0].total).toBe(16);
    expect(points[1].total).toBe(16);
  });

  it("reads each bot's trading pair from its own live controller", () => {
    // `pairByCtrl` is keyed the same way, so a bot quoting in USDC is not
    // converted through its namesake's USDT pair.
    const convert = vi.fn((value: number, quote: string) => ({
      value: quote === "USDC" ? value * 2 : value,
      converted: quote === "USDC",
    }));
    aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), trading_pair: "", realized_pnl_quote: 1 }),
        beta({ timestamp: at("10:00"), trading_pair: "", realized_pnl_quote: 1 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [
        ctrl({ bot_name: "alpha", controller_id: shared, trading_pair: "SOL-USDT" }),
        ctrl({ bot_name: "beta", controller_id: shared, trading_pair: "SOL-USDC" }),
      ],
      convert,
    );

    expect(convert).toHaveBeenCalledWith(1, "USDT");
    expect(convert).toHaveBeenCalledWith(1, "USDC");
  });
});

/**
 * The parsing budget (PERF-282).
 *
 * `timestamp` is an ISO string, so every read of it through `toMs` is a
 * `Date.parse`. Read straight off the snapshot it is paid once per *comparison*
 * in the per-controller sort, once more per row for the timeline, and again on
 * every (instant × controller) pair of the forward-fill — which on a fleet-sized
 * history is six figures of parsing, redone each time the `bots` socket mints a
 * new `controllers` array. The fold decorates each row with its epoch-ms once
 * instead, so this pins the count at one parse per snapshot.
 */
describe("aggregatePnlSeries — parses each timestamp once", () => {
  it("makes no more Date.parse calls than it has snapshots", () => {
    const CONTROLLERS = 3;
    const PER_CONTROLLER = 200;

    // Built before the spy: the fixtures' own timestamps must not be counted.
    const base = Date.parse("2026-08-27T00:00:00Z");
    const snapshots: ControllerPerformanceSnapshot[] = [];
    for (let c = 0; c < CONTROLLERS; c++) {
      for (let i = 0; i < PER_CONTROLLER; i++) {
        snapshots.push(
          snap({
            controller_id: `ctrl-${c}`,
            controller_name: `ctrl-${c}`,
            // Interleaved and out of order, so the sort has real work to do.
            timestamp: new Date(base + ((i * 7919) % PER_CONTROLLER) * 60_000).toISOString(),
            realized_pnl_quote: i,
            volume_traded: i * 10,
          }),
        );
      }
    }
    const enabled = new Set(Array.from({ length: CONTROLLERS }, (_, c) => key(`ctrl-${c}`)));

    const parse = vi.spyOn(Date, "parse");
    try {
      const points = aggregatePnlSeries(snapshots, enabled, []);
      expect(points.length).toBeGreaterThan(0);
      expect(parse).toHaveBeenCalledTimes(snapshots.length);
    } finally {
      parse.mockRestore();
    }
  });

  it("folds identically however the rows are ordered on the way in", () => {
    const rows = [
      snap({ timestamp: at("10:00"), realized_pnl_quote: 1, volume_traded: 10 }),
      snap({ timestamp: at("10:30"), realized_pnl_quote: 2, volume_traded: 30 }),
      snap({ controller_id: "ctrl-b", controller_name: "ctrl-b", timestamp: at("10:15"), realized_pnl_quote: 7, volume_traded: 5 }),
      snap({ controller_id: "ctrl-b", controller_name: "ctrl-b", timestamp: at("10:45"), realized_pnl_quote: 9, volume_traded: 25 }),
    ];
    const enabled = new Set([key("ctrl-a"), key("ctrl-b")]);

    const inOrder = aggregatePnlSeries(rows, enabled, []);
    const shuffled = aggregatePnlSeries([rows[3], rows[0], rows[2], rows[1]], enabled, []);

    expect(shuffled).toEqual(inOrder);
    expect(inOrder.map((p) => p.time)).toEqual([ms("10:00"), ms("10:15"), ms("10:30"), ms("10:45")]);
    expect(inOrder.map((p) => p.realized)).toEqual([1, 8, 9, 11]);
  });
});

describe("positionQuoteValue", () => {
  it("is zero for no positions", () => {
    expect(positionQuoteValue([])).toBe(0);
  });

  it("signs shorts negative and longs positive", () => {
    expect(positionQuoteValue([{ amount: 2, breakeven_price: 10, side: "BUY" }])).toBe(20);
    expect(positionQuoteValue([{ amount: 2, breakeven_price: 10, side: "SELL" }])).toBe(-20);
    expect(positionQuoteValue([{ net_amount_base: 2, entry_price: 10, position_side: "SHORT" }])).toBe(-20);
  });

  it("prefers breakeven price, then entry, then current", () => {
    expect(positionQuoteValue([{ amount: 1, breakeven_price: 3, entry_price: 5, current_price: 7 }])).toBe(3);
    expect(positionQuoteValue([{ amount: 1, entry_price: 5, current_price: 7 }])).toBe(5);
    expect(positionQuoteValue([{ amount: 1, current_price: 7 }])).toBe(7);
  });
});

/**
 * The position axis (READ-246).
 *
 * The series is drawn as an area filled from zero because its sign is what it
 * is *for* — `positionQuoteValue` above negates shorts, so a net-short book is
 * a negative number and the reader has to be able to see that at a glance. An
 * area from zero only says that if zero is on the axis, and recharts will not
 * put it there on its own: a user-provided bound is widened to fit the data,
 * never narrowed, so the default `[0, "auto"]` collapses to the data's own
 * range the moment any value is negative.
 */
/** A bare timeline carrying nothing but the positions a case cares about. */
const series = (...positions: number[]): PnlChartPoint[] =>
  positions.map((position, i) => ({
    time: 1_000 * (i + 1),
    realized: 0,
    unrealized: 0,
    total: 0,
    volume: 0,
    volumeDelta: 0,
    position,
  }));

describe("positionAxisDomain", () => {
  it("straddles zero for a book that never leaves one side", () => {
    const [longMin, longMax] = positionAxisDomain(series(400, 1_200));
    expect(longMin).toBeLessThan(0);
    expect(longMax).toBeGreaterThanOrEqual(1_200);

    const [shortMin, shortMax] = positionAxisDomain(series(-400, -1_200));
    expect(shortMin).toBeLessThanOrEqual(-1_200);
    expect(shortMax).toBeGreaterThan(0);
  });

  it("covers both extremes of a book that flips", () => {
    const [min, max] = positionAxisDomain(series(900, -500, 300));
    expect(min).toBeLessThanOrEqual(-500);
    expect(max).toBeGreaterThanOrEqual(900);
  });

  it("pads by a fraction of the span it is padding", () => {
    // 0..1000 spans 1000, so each end gets POSITION_AXIS_PAD of that.
    expect(positionAxisDomain(series(0, 1_000))).toEqual([
      -1_000 * POSITION_AXIS_PAD,
      1_000 * (1 + POSITION_AXIS_PAD),
    ]);
  });

  it("stays a usable domain for an empty or flat series", () => {
    for (const data of [[], series(0, 0)]) {
      const [min, max] = positionAxisDomain(data);
      expect(min).toBeLessThan(0);
      expect(max).toBeGreaterThan(0);
    }
  });
});

describe("positionAreaExtent", () => {
  it("clamps both ends through zero, because the fill starts there", () => {
    expect(positionAreaExtent(series(400, 1_200))).toEqual([0, 1_200]);
    expect(positionAreaExtent(series(-400, -1_200))).toEqual([-1_200, 0]);
    expect(positionAreaExtent(series(900, -500))).toEqual([-500, 900]);
    expect(positionAreaExtent([])).toEqual([0, 0]);
  });

  it("is the unpadded inside of the axis domain", () => {
    const data = series(900, -500);
    const [extentMin, extentMax] = positionAreaExtent(data);
    const [domainMin, domainMax] = positionAxisDomain(data);
    expect(domainMin).toBeLessThan(extentMin);
    expect(domainMax).toBeGreaterThan(extentMax);
  });
});

/**
 * The gradient's units are the *fill's* bounding box, not the plot area
 * (objectBoundingBox is the SVG default), so the offset is taken from the
 * area's extent. Handing it the padded domain instead paints a sliver of the
 * opposite side's colour along the baseline of a one-sided book — which is
 * precisely the misreading this item exists to remove.
 */
describe("zeroGradientOffset", () => {
  it("puts the colour change where zero falls, measured from the top", () => {
    expect(zeroGradientOffset([-100, 100])).toBeCloseTo(0.5, 10);
    expect(zeroGradientOffset([-300, 100])).toBeCloseTo(0.25, 10);
    expect(zeroGradientOffset([-100, 300])).toBeCloseTo(0.75, 10);
  });

  it("collapses to a single colour when the series never changes sign", () => {
    expect(zeroGradientOffset(positionAreaExtent(series(400, 1_200)))).toBe(1);
    expect(zeroGradientOffset(positionAreaExtent(series(-400, -1_200)))).toBe(0);
  });

  it("never leaves the 0..1 range an SVG stop accepts", () => {
    for (const extent of [[0, 0], [5, 5], [100, -100], [-1, 0], [0, 1]] as Array<[number, number]>) {
      const offset = zeroGradientOffset(extent);
      expect(offset).toBeGreaterThanOrEqual(0);
      expect(offset).toBeLessThanOrEqual(1);
    }
  });
});

/**
 * Interval selection (PERF-238).
 *
 * The rule under test is one sentence — pick the finest interval upstream
 * accepts whose point count over the span fits the budget — and the reason it
 * is worth pinning is that both halves of it are external facts that can drift:
 * the accepted set is a regex on the server (`^(5m|15m|30m|1h|4h|12h|1d)$`,
 * anything else is a 422) and the budget is the route's `limit` ceiling of
 * 1000. A test per threshold makes a change to either one fail loudly here
 * rather than quietly on a chart.
 */
describe("pickSamplingInterval", () => {
  const MIN = 60_000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;

  it("only ever returns a value the endpoint accepts", () => {
    const accepted = /^(5m|15m|30m|1h|4h|12h|1d)$/;
    // Every interval in the ladder is one upstream takes...
    for (const iv of SAMPLING_INTERVALS) expect(iv).toMatch(accepted);
    // ...and no span, however absurd, can produce anything outside it.
    for (const span of [1, MIN, HOUR, DAY, 400 * DAY, 100_000 * DAY, Number.MAX_SAFE_INTEGER]) {
      expect(pickSamplingInterval(span)).toMatch(accepted);
    }
  });

  it("keeps 5m for a bot deployed minutes or hours ago", () => {
    expect(pickSamplingInterval(10 * MIN)).toBe("5m");
    expect(pickSamplingInterval(HOUR)).toBe("5m");
    expect(pickSamplingInterval(DAY)).toBe("5m");
  });

  it("steps up one rung at each budget threshold", () => {
    // The exact boundary for each rung is budget * interval; one millisecond
    // past it the rung no longer fits and the next one is chosen.
    const boundaries: [number, string, string][] = [
      [HISTORY_POINT_BUDGET * 5 * MIN, "5m", "15m"],
      [HISTORY_POINT_BUDGET * 15 * MIN, "15m", "30m"],
      [HISTORY_POINT_BUDGET * 30 * MIN, "30m", "1h"],
      [HISTORY_POINT_BUDGET * HOUR, "1h", "4h"],
      [HISTORY_POINT_BUDGET * 4 * HOUR, "4h", "12h"],
      [HISTORY_POINT_BUDGET * 12 * HOUR, "12h", "1d"],
    ];
    for (const [edge, atEdge, past] of boundaries) {
      expect(pickSamplingInterval(edge)).toBe(atEdge);
      expect(pickSamplingInterval(edge + 1)).toBe(past);
    }
  });

  it("asks for hourly points, not 5-minute ones, for a month-old fleet", () => {
    // The case the item is about: 30 days at 5m is 8,640 points, of which the
    // route would return the first 1000.
    expect(pickSamplingInterval(30 * DAY)).toBe("1h");
    expect(30 * DAY / HOUR).toBeLessThanOrEqual(HISTORY_POINT_BUDGET);
  });

  it("never exceeds the budget, and never coarsens further than it must", () => {
    const ms: Record<string, number> = {
      "5m": 5 * MIN, "15m": 15 * MIN, "30m": 30 * MIN,
      "1h": HOUR, "4h": 4 * HOUR, "12h": 12 * HOUR, "1d": DAY,
    };
    for (let days = 1; days <= 600; days++) {
      const span = days * DAY;
      const chosen = pickSamplingInterval(span);
      expect(Math.ceil(span / ms[chosen])).toBeLessThanOrEqual(HISTORY_POINT_BUDGET);
      // and the rung below it genuinely did not fit
      const finer = SAMPLING_INTERVALS[SAMPLING_INTERVALS.indexOf(chosen) - 1];
      if (finer) expect(Math.ceil(span / ms[finer])).toBeGreaterThan(HISTORY_POINT_BUDGET);
    }
  });

  it("saturates at 1d rather than inventing a coarser interval", () => {
    // 1d over 10,000 days is 10,000 points, well past the budget — but "1w" is
    // a 422, so the ladder's last rung is the answer, not the next power of ten.
    expect(pickSamplingInterval(10_000 * DAY)).toBe("1d");
  });

  it("falls back to the finest interval when the span is unknown or nonsense", () => {
    // Not knowing how far back the window goes must not cost detail for a bot
    // that started ten minutes ago, so "unknown" means 5m — the old behaviour.
    expect(pickSamplingInterval(undefined)).toBe("5m");
    expect(pickSamplingInterval(0)).toBe("5m");
    expect(pickSamplingInterval(-DAY)).toBe("5m");
    expect(pickSamplingInterval(NaN)).toBe("5m");
    expect(pickSamplingInterval(Infinity)).toBe("5m");
  });

  it("honours a caller-supplied budget", () => {
    expect(pickSamplingInterval(DAY, 100)).toBe("15m");
    expect(pickSamplingInterval(DAY, 10)).toBe("4h");
  });
});

describe("samplingIntervalSince", () => {
  const DAY = 24 * 60 * 60_000;

  it("measures the span from a deploy timestamp to now", () => {
    expect(samplingIntervalSince("2026-08-27T11:00:00Z", NOW)).toBe("5m");
    expect(samplingIntervalSince(new Date(NOW - 30 * DAY).toISOString(), NOW)).toBe("1h");
    expect(samplingIntervalSince(new Date(NOW - 300 * DAY).toISOString(), NOW)).toBe("12h");
  });

  it("returns 5m when there is no usable start time", () => {
    // The callers pass `deployed_at`/`earliestDeploy` straight through, and both
    // are optional: no start time is an unbounded request, not a wide one.
    expect(samplingIntervalSince(undefined, NOW)).toBe("5m");
    expect(samplingIntervalSince(null, NOW)).toBe("5m");
    expect(samplingIntervalSince("", NOW)).toBe("5m");
    expect(samplingIntervalSince("not a date", NOW)).toBe("5m");
  });

  it("treats a start time in the future as unknown rather than coarse", () => {
    expect(samplingIntervalSince(new Date(NOW + DAY).toISOString(), NOW)).toBe("5m");
  });
});

/**
 * Per-interval volume (READ-245).
 *
 * `volume_traded` is a running counter, so the series drawn from it could only
 * ever slope up-right. `volumeDelta` is the flow behind that stock, and every
 * case here is a way the *summed* series would get it wrong — which is the
 * whole reason the diff is taken per controller, inside the fold, rather than
 * by a helper differencing `volume` afterwards.
 */
describe("aggregatePnlSeries — volumeDelta", () => {
  const shared = "grid-1";
  const alpha = (over: Partial<ControllerPerformanceSnapshot> = {}) =>
    snap({ bot_name: "alpha", controller_id: shared, ...over });
  const beta = (over: Partial<ControllerPerformanceSnapshot> = {}) =>
    snap({ bot_name: "beta", controller_id: shared, ...over });

  const deltas = (points: PnlChartPoint[]) => points.map((p) => p.volumeDelta);

  it("differences the running counter into what each bucket actually traded", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), volume_traded: 100 }),
        snap({ timestamp: at("10:05"), volume_traded: 100 }),
        snap({ timestamp: at("10:10"), volume_traded: 450 }),
        snap({ timestamp: at("10:15"), volume_traded: 470 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    // The cumulative series climbs and never falls; the deltas say *when* the
    // trading happened — and 10:05 says "nothing", which the ramp could not.
    expect(points.map((p) => p.volume)).toEqual([100, 100, 450, 470]);
    expect(deltas(points)).toEqual([0, 0, 350, 20]);
  });

  it("draws no bar for a controller's opening reading, whatever the counter already says", () => {
    // The first reading has no predecessor to be a difference from, and its
    // absolute value is everything the controller has ever traded — the one
    // quantity this series exists to stop drawing. Charged to the opening
    // bucket it becomes a bar millions high and the axis scales to it, which
    // flattens every real bucket to a pixel.
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), volume_traded: 11_600_000 }),
        snap({ timestamp: at("10:05"), volume_traded: 11_600_400 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    expect(deltas(points)).toEqual([0, 400]);
  });

  it("totals the volume traded across the drawn window", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), volume_traded: 40 }),
        snap({ timestamp: at("10:05"), volume_traded: 90 }),
        snap({ timestamp: at("10:10"), volume_traded: 91.5 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    // The bars are the flow whose stock is `volume`: they add up to how much
    // the counter moved between the ends of the window. They do *not* add up
    // to the header's lifetime Vol stat, and are not meant to — that is the
    // stock, this is the flow, and the tooltip names the bucket so the two
    // cannot be mistaken for each other.
    const summed = points.reduce((acc, p) => acc + p.volumeDelta, 0);
    expect(summed).toBeCloseTo(points[points.length - 1].volume - points[0].volume, 10);
  });

  it("charges a bucket in which a controller stood still nothing at all", () => {
    // beta reports at 10:05 and 10:10 and is forward-filled at 10:15. Its
    // filled bucket must diff to zero — otherwise every idle controller would
    // keep drawing bars for as long as the chart ran.
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), volume_traded: 10 }),
        alpha({ timestamp: at("10:05"), volume_traded: 30 }),
        beta({ timestamp: at("10:05"), volume_traded: 70 }),
        beta({ timestamp: at("10:10"), volume_traded: 95 }),
        alpha({ timestamp: at("10:15"), volume_traded: 30 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [],
    );
    expect(points.map((p) => p.volume)).toEqual([10, 100, 125, 125]);
    // 10:05 is alpha's 20 alone (beta is opening); 10:10 is beta's 25 alone;
    // 10:15 neither moved.
    expect(deltas(points)).toEqual([0, 20, 25, 0]);
  });

  it("never turns a controller joining mid-window into a bar for the whole fleet", () => {
    // The sum jumps 10 -> 5010 when beta appears, because before its first
    // snapshot beta contributes nothing at all. Differencing the summed series
    // would read that as $5,000 traded in one bucket by a fleet that in fact
    // stood still — and alpha's stillness stays visible in the same instant.
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), volume_traded: 10 }),
        alpha({ timestamp: at("10:05"), volume_traded: 10 }),
        beta({ timestamp: at("10:05"), volume_traded: 5_000 }),
        beta({ timestamp: at("10:10"), volume_traded: 5_060 }),
        alpha({ timestamp: at("10:10"), volume_traded: 10 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [],
    );
    expect(points.map((p) => p.volume)).toEqual([10, 5_010, 5_070]);
    expect(deltas(points)).toEqual([0, 0, 60]);
  });

  it("never draws a negative bar when a controller restarts and its counter resets", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), volume_traded: 900 }),
        snap({ timestamp: at("10:05"), volume_traded: 940 }),
        snap({ timestamp: at("10:10"), volume_traded: 20 }),
        snap({ timestamp: at("10:15"), volume_traded: 55 }),
      ],
      new Set([key("ctrl-a")]),
      [],
    );
    // The reset itself is not tradeable volume, so it is worth zero — and the
    // counter is then followed from its new base rather than being abandoned.
    expect(deltas(points)).toEqual([0, 40, 0, 35]);
  });

  it("clamps the reset per controller, so one restart cannot erase another's trading", () => {
    // alpha resets 900 -> 0 in the same bucket beta trades 300. On the summed
    // series that is 1000 -> 400, a fall, and a clamp there would report the
    // fleet as idle in the one bucket it was busiest.
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), volume_traded: 900 }),
        beta({ timestamp: at("10:00"), volume_traded: 100 }),
        alpha({ timestamp: at("10:05"), volume_traded: 0 }),
        beta({ timestamp: at("10:05"), volume_traded: 400 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [],
    );
    expect(points.map((p) => p.volume)).toEqual([1_000, 400]);
    expect(deltas(points)).toEqual([0, 300]);
  });

  it("measures the live point against each controller's last snapshot", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [
        alpha({ timestamp: at("10:00"), volume_traded: 200 }),
        beta({ timestamp: at("10:00"), volume_traded: 50 }),
      ],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [
        ctrl({ bot_name: "alpha", controller_id: shared, volume_traded: 260 }),
        ctrl({ bot_name: "beta", controller_id: shared, volume_traded: 50 }),
      ],
    );
    // alpha has traded 60 since its last stored snapshot, beta nothing: the
    // final bar is an in-progress bucket, honestly short until it fills.
    expect(deltas(points)).toEqual([0, 60]);
  });

  it("gives no bar to a live controller that has no stored snapshot to be measured from", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const points = aggregatePnlSeries(
      [alpha({ timestamp: at("10:00"), volume_traded: 200 })],
      new Set([key(shared, "alpha"), key(shared, "beta")]),
      [
        ctrl({ bot_name: "alpha", controller_id: shared, volume_traded: 200 }),
        // beta is enabled and live but contributed no history: its lifetime
        // counter is an opening reading like any other, not a bucket.
        ctrl({ bot_name: "beta", controller_id: shared, volume_traded: 900_000 }),
      ],
    );
    expect(deltas(points)).toEqual([0, 0]);
    expect(points[points.length - 1].volume).toBe(900_200);
  });

  it("converts before differencing, so a bar is in the same currency as the total", () => {
    const points = aggregatePnlSeries(
      [
        snap({ timestamp: at("10:00"), volume_traded: 100 }),
        snap({ timestamp: at("10:05"), volume_traded: 300 }),
      ],
      new Set([key("ctrl-a")]),
      [],
      (val: number) => ({ value: val * 2, converted: true }),
    );
    expect(deltas(points)).toEqual([0, 400]);
  });
});

/**
 * Bar geometry (READ-245).
 *
 * recharts sizes a bar on a numeric axis from the *smallest* gap between two
 * adjacent points and clamps an explicit `barSize` back under it, so these two
 * helpers are what stop one short gap — the live "now" point, a few seconds
 * after the last snapshot — from setting the width of every bar in the pane.
 */
describe("chartBucketMs", () => {
  const point = (time: number): PnlChartPoint => ({
    time,
    realized: 0,
    unrealized: 0,
    total: 0,
    volume: 0,
    volumeDelta: 0,
    position: 0,
  });
  const MIN = 60_000;

  it("has no bucket to report for a series too short to have a gap", () => {
    expect(chartBucketMs([])).toBe(0);
    expect(chartBucketMs([point(0)])).toBe(0);
  });

  it("reports the spacing of an evenly sampled series", () => {
    expect(chartBucketMs([0, 5, 10, 15, 20].map((m) => point(m * MIN)))).toBe(5 * MIN);
  });

  it("ignores the short final gap the live point leaves behind", () => {
    // This is the case the whole helper exists for: five 5m buckets and then
    // "now", four seconds after the last snapshot. The minimum gap is 4s and
    // would draw every bar 4/300ths of a bucket wide.
    const times = [0, 5 * MIN, 10 * MIN, 15 * MIN, 20 * MIN, 20 * MIN + 4_000];
    expect(chartBucketMs(times.map(point))).toBe(5 * MIN);
  });

  it("absorbs a gap that is too wide as well as one that is too narrow", () => {
    // A missing bucket at one end and the live point at the other: the typical
    // spacing is unmoved, which is the property a mean would not have.
    const times = [0, 5 * MIN, 15 * MIN, 20 * MIN, 25 * MIN, 25 * MIN + 3_000];
    expect(chartBucketMs(times.map(point))).toBe(5 * MIN);
  });

  it("follows the majority when a merged timeline really does tick twice a bucket", () => {
    // Two controllers persistently a couple of seconds out of step is not a
    // stray gap, it is the series' actual spacing — half a bucket's trading
    // lands on each point — and the bars narrow to match rather than being
    // drawn a bucket wide and overlapping each other.
    const times = [0, 2_000, 5 * MIN, 5 * MIN + 2_000, 10 * MIN, 10 * MIN + 2_000];
    expect(chartBucketMs(times.map(point))).toBe(2_000);
  });

  it("tracks whichever rung of the sampling ladder the series was fetched at", () => {
    for (const bucket of [5 * MIN, 60 * MIN, 4 * 60 * MIN, 24 * 60 * MIN]) {
      const times = [0, 1, 2, 3, 4, 5].map((i) => point(i * bucket));
      expect(chartBucketMs(times)).toBe(bucket);
    }
  });
});

describe("volumeBarWidth", () => {
  it("gives a bar its bucket's share of the plot", () => {
    // 60 buckets across 1200px is 20px each; the duty cycle leaves the gap that
    // makes it a bar rather than a filled block.
    expect(volumeBarWidth(1200, 60 * 60_000, 60_000)).toBeCloseTo(20 * VOLUME_BAR_DUTY, 6);
  });

  it("is the same bar at every rung of the sampling ladder", () => {
    // A 5m bucket on a 2-day window and a 1d bucket on a 576-day one are the
    // same fraction of the axis, which is exactly why the width is derived
    // from the proportion rather than from the interval's name.
    const fine = volumeBarWidth(900, 2 * 24 * 60 * 60_000, 5 * 60_000);
    const coarse = volumeBarWidth(900, 576 * 24 * 60 * 60_000, 24 * 60 * 60_000);
    expect(fine).toBeCloseTo(coarse!, 6);
  });

  it("keeps a dense window's bars visible and a sparse window's bars from becoming blocks", () => {
    // 5,000 buckets across 900px is a fifth of a pixel; 2 buckets is 450.
    expect(volumeBarWidth(900, 5_000 * 60_000, 60_000)).toBe(VOLUME_BAR_MIN_PX);
    expect(volumeBarWidth(900, 2 * 60_000, 60_000)).toBe(VOLUME_BAR_MAX_PX);
  });

  it("declines to answer before the pane has been measured, leaving recharts its default", () => {
    expect(volumeBarWidth(0, 60_000, 5_000)).toBeUndefined();
    expect(volumeBarWidth(-40, 60_000, 5_000)).toBeUndefined();
    expect(volumeBarWidth(900, 0, 5_000)).toBeUndefined();
    expect(volumeBarWidth(900, 60_000, 0)).toBeUndefined();
  });
});

describe("formatBucketLabel", () => {
  it("names the bucket with the same word the history request used", () => {
    expect(formatBucketLabel(5 * 60_000)).toBe("5m");
    expect(formatBucketLabel(60 * 60_000)).toBe("1h");
    expect(formatBucketLabel(24 * 60 * 60_000)).toBe("1d");
  });

  it("snaps a spacing that is a few seconds off a round interval", () => {
    expect(formatBucketLabel(15 * 60_000 + 3_000)).toBe("15m");
    expect(formatBucketLabel(4 * 60 * 60_000 - 11_000)).toBe("4h");
  });

  it("snaps in proportion, not in absolute time", () => {
    // Linearly, 8m is far nearer 5m than 15m in the sense that matters to a
    // reader — it is 1.6x one and 0.53x the other — and a distance measured in
    // milliseconds would drag everything toward the long end of the ladder.
    expect(formatBucketLabel(8 * 60_000)).toBe("5m");
    expect(formatBucketLabel(11 * 60_000)).toBe("15m");
  });

  it("has nothing to name when the series has no spacing", () => {
    expect(formatBucketLabel(0)).toBeUndefined();
  });
});

describe("resolveTimeRange (READ-249)", () => {
  /** An hourly series over four hours. */
  const hourly = [0, 1, 2, 3, 4].map((h) => ({
    time: 10_000_000 + h * 3_600_000,
    realized: h,
    unrealized: 0,
    total: h,
    volume: h * 100,
    volumeDelta: 100,
    position: 0,
  }));
  const first = hourly[0].time;
  const last = hourly[hourly.length - 1].time;

  it("defaults to the whole loaded window", () => {
    expect(resolveTimeRange(hourly, null)).toEqual([first, last]);
  });

  it("measures a trailing window back from the newest point, so it slides", () => {
    expect(resolveTimeRange(hourly, { start: null, end: null, trailing: 2 * 3_600_000 })).toEqual([
      last - 2 * 3_600_000,
      last,
    ]);

    // The same selection against a series that has grown by an hour: the width
    // is kept and the window has moved with the data, which is what "following
    // the live edge" has to mean.
    const grown = [...hourly, { ...hourly[4], time: last + 3_600_000 }];
    expect(resolveTimeRange(grown, { start: null, end: null, trailing: 2 * 3_600_000 })).toEqual([
      last - 3_600_000,
      last + 3_600_000,
    ]);
  });

  it("leaves a window that does not touch the live edge exactly where it was put", () => {
    const frozen = { start: first, end: first + 2 * 3_600_000 };
    const grown = [...hourly, { ...hourly[4], time: last + 3_600_000 }];
    // Two more socket frames' worth of points arrive; the selection does not move.
    expect(resolveTimeRange(grown, frozen)).toEqual([frozen.start, frozen.end]);
    expect(resolveTimeRange([...grown, { ...hourly[4], time: last + 7_200_000 }], frozen)).toEqual([
      frozen.start,
      frozen.end,
    ]);
  });

  it("clamps a selection that reaches past either end of the data", () => {
    expect(resolveTimeRange(hourly, { start: first - 99_999_999, end: last + 99_999_999 })).toEqual([
      first,
      last,
    ]);
  });

  it("falls back to the full window rather than to a slice that cannot be drawn", () => {
    // A controller chip toggled off, or a refetch at a coarser interval, can
    // leave the stored window holding no points at all — the case that would
    // otherwise blank both panes, and the one an index-based brush turns into
    // an out-of-range index.
    const elsewhere = hourly.map((p) => ({ ...p, time: p.time + 30 * 86_400_000 }));
    expect(resolveTimeRange(elsewhere, { start: first, end: first + 60_000 })).toEqual([
      elsewhere[0].time,
      elsewhere[elsewhere.length - 1].time,
    ]);
    // ...and one point is not a series either.
    expect(resolveTimeRange(hourly, { start: first - 1, end: first + 1 })).toEqual([first, last]);
  });

  it("has nothing to resolve against an empty series", () => {
    expect(resolveTimeRange([], null)).toEqual([0, 0]);
    expect(resolveTimeRange([], { start: 1, end: 2 })).toEqual([0, 0]);
  });

  describe("sliceToRange", () => {
    it("hands back the very same array when the window is everything", () => {
      // Identity, not just equality: both panes pass this straight to recharts
      // as their `data`, and a fresh array on every render would re-derive every axis.
      expect(sliceToRange(hourly, first, last)).toBe(hourly);
      expect(sliceToRange(hourly, first - 1_000, last + 1_000)).toBe(hourly);
    });

    it("keeps the points inside the window, ends included", () => {
      const slice = sliceToRange(hourly, hourly[1].time, hourly[3].time);
      expect(slice.map((p) => p.time)).toEqual([hourly[1].time, hourly[2].time, hourly[3].time]);
    });
  });
});

// ── executorSeries (FEAT-086) ──

describe("executorSeries", () => {
  const MIN = 60_000;
  const T0 = Date.parse("2026-09-01T00:00:00Z");

  /** A closed outcome: a final PnL and a volume, at a known instant. */
  const closed = (
    endedAt: number | null,
    net: number,
    volume = 0,
    pair = "SOL-USDC",
  ): ClosedOutcome => ({ endedAt, net, volume, pair });

  const identity = (value: number) => value;

  it("has nothing to draw when nothing has closed", () => {
    expect(executorSeries([], identity)).toEqual([]);
    expect(executorSeries([closed(null, 5)], identity)).toEqual([]);
  });

  it("draws a running cumulative sum of outcomes", () => {
    const series = executorSeries(
      [closed(T0 + 5 * MIN, 10, 100), closed(T0 + 15 * MIN, -4, 60), closed(T0 + 25 * MIN, 7, 40)],
      identity,
    );
    // An opening zero, then one point per bucket.
    expect(series[0]).toMatchObject({ total: 0, realized: 0, volume: 0, volumeDelta: 0 });
    expect(series.map((p) => p.total)).toEqual([0, 10, 6, 13]);
    expect(series.map((p) => p.volume)).toEqual([0, 100, 160, 200]);
    expect(series.map((p) => p.volumeDelta)).toEqual([0, 100, 60, 40]);
  });

  it("banks everything as realized and holds nothing", () => {
    // A closed set has no open position and no mark to be marked to, and the
    // chart reads an all-zero position series as "no position pane".
    for (const point of executorSeries([closed(T0, 10, 5), closed(T0 + MIN, 2, 5)], identity)) {
      expect(point.unrealized).toBe(0);
      expect(point.position).toBe(0);
      expect(point.realized).toBe(point.total);
    }
  });

  it("does not care what order the outcomes arrive in", () => {
    const forwards = executorSeries([closed(T0, 1), closed(T0 + 10 * MIN, 2)], identity);
    const backwards = executorSeries([closed(T0 + 10 * MIN, 2), closed(T0, 1)], identity);
    expect(backwards).toEqual(forwards);
  });

  it("sums outcomes that closed inside the same bucket into one point", () => {
    const series = executorSeries(
      [closed(T0 + MIN, 3, 10), closed(T0 + 2 * MIN, 4, 20), closed(T0 + 30 * MIN, 1, 5)],
      identity,
    );
    // Three outcomes, two buckets — plus the opening zero.
    expect(series).toHaveLength(3);
    expect(series[1]).toMatchObject({ total: 7, volumeDelta: 30 });
    expect(series[2]).toMatchObject({ total: 8, volumeDelta: 5 });
  });

  it("keeps the point count inside the budget however long the span", () => {
    // A year of daily closes. The sampling ladder stops at a day, which is a
    // constraint of the history route rather than of this series, so the bucket
    // widens past it rather than the chart drawing one point per executor.
    const leaves = Array.from({ length: 365 }, (_, i) => closed(T0 + i * 86_400_000, 1, 1));
    const series = executorSeries(leaves, identity, 100);
    expect(series.length).toBeLessThanOrEqual(101);
    expect(series[series.length - 1].total).toBe(365);
    expect(series[series.length - 1].volume).toBe(365);
  });

  it("ends at the last close rather than at the end of an unfinished bucket", () => {
    const last = T0 + 7 * MIN;
    const series = executorSeries([closed(T0 + MIN, 1), closed(last, 1)], identity);
    expect(series[series.length - 1].time).toBe(last);
  });

  it("starts flat at zero, so the first outcome reads as a step up from nothing", () => {
    const series = executorSeries([closed(T0 + 3 * MIN, 12)], identity);
    expect(series).toHaveLength(2);
    expect(series[0].total).toBe(0);
    expect(series[0].time).toBeLessThan(series[1].time);
    expect(series[1].total).toBe(12);
  });

  it("converts each outcome through its own pair", () => {
    const cv = (value: number, pair: string) => (pair.endsWith("-EUR") ? value * 2 : value);
    const series = executorSeries(
      [closed(T0, 10, 100, "SOL-USDC"), closed(T0 + 10 * MIN, 10, 100, "SOL-EUR")],
      cv,
    );
    expect(series[series.length - 1].total).toBe(30);
    expect(series[series.length - 1].volume).toBe(300);
  });

  it("rises and falls with the outcomes rather than only rising", () => {
    // Unlike the sampled series, whose volume counter can only go up, this one
    // draws PnL that a losing run actually takes back.
    const series = executorSeries(
      [closed(T0, 10), closed(T0 + 10 * MIN, -30), closed(T0 + 20 * MIN, 5)],
      identity,
    );
    expect(series.map((p) => p.total)).toEqual([0, 10, -20, -15]);
  });
});


/**
 * A finished run's cached points draw *the same chart* a live fleet draws
 * (FEAT-089).
 *
 * That is the objective of the whole feature, and it is only true if the
 * expansion is exact: the same fold, the same forward-fill, the same series. So
 * the tests are about the two things an expansion can quietly get wrong — the
 * identity a value is converted through, and a position series invented out of
 * a PnL that has none.
 */
describe("snapshotsFromRunHistory", () => {
  const history = {
    controllers: {
      c1: [
        [1_700_000_000_000, 10, 2, 12, 500, 0.05],
        [1_700_000_300_000, 20, 3, 23, 900, 0.09],
      ],
      c2: [[1_700_000_000_000, 5, 0, 5, 100, 0.01]],
    },
    identities: {
      c1: { connector: "binance", trading_pair: "BTC-BRL" },
      c2: { connector: "binance", trading_pair: "SOL-USDC" },
    },
  };

  it("expands every controller's points, keyed so the fold can find them", () => {
    const snaps = snapshotsFromRunHistory(history, "gan");
    expect(snaps).toHaveLength(3);
    expect(new Set(snaps.map((s) => controllerKey(s)))).toEqual(
      new Set(["gan:c1", "gan:c2"]),
    );
  });

  it("carries each controller's own pair, so a BRL run is not folded as dollars", () => {
    const snaps = snapshotsFromRunHistory(history, "gan");
    expect(snaps.find((s) => s.controller_id === "c1")?.trading_pair).toBe("BTC-BRL");
    expect(snaps.find((s) => s.controller_id === "c2")?.trading_pair).toBe("SOL-USDC");
  });

  it("keeps the realized/unrealized split the terminated chart exists to show", () => {
    const [first] = snapshotsFromRunHistory(history, "gan");
    expect(first.realized_pnl_quote).toBe(10);
    expect(first.unrealized_pnl_quote).toBe(2);
    expect(first.global_pnl_quote).toBe(12);
    expect(first.volume_traded).toBe(500);
  });

  // A finished run holds nothing. Synthesising a position out of the PnL would
  // draw one nobody holds.
  it("holds no position, because a run that has stopped holds none", () => {
    for (const snap of snapshotsFromRunHistory(history, "gan")) {
      expect(snap.positions_summary).toEqual([]);
    }
  });

  it("feeds aggregatePnlSeries the same way live snapshots do", () => {
    const snaps = snapshotsFromRunHistory(history, "gan");
    const series = aggregatePnlSeries(snaps, new Set(["gan:c1", "gan:c2"]), []);
    expect(series.length).toBeGreaterThan(1);
    // Both controllers folded at the first instant: 10 + 5 realized.
    expect(series[0].realized).toBe(15);
    // c2 forward-fills at the second instant: 20 + 5.
    expect(series[series.length - 1].realized).toBe(25);
  });

  // `aggregatePnlSeries` ends a series with a "now" point so a *live* chart
  // reaches real time. A run that stopped last week has no "now", and giving it
  // one draws a flat line from its final trade to this instant — which reads as
  // a bot still holding a position.
  it("ends where the trading ended when no live controller is supplied", () => {
    const snaps = snapshotsFromRunHistory(history, "gan");
    const series = aggregatePnlSeries(snaps, new Set(["gan:c1", "gan:c2"]), []);
    expect(series[series.length - 1].time).toBe(1_700_000_300_000);
    expect(series[series.length - 1].time).toBeLessThan(Date.now());
  });

  it("expands an empty history to nothing rather than to a flat line", () => {
    expect(snapshotsFromRunHistory({ controllers: {}, identities: {} }, "gan")).toEqual([]);
  });

  it("survives a controller whose identity was never resolved", () => {
    const [snap] = snapshotsFromRunHistory(
      { controllers: { c1: [[1, 0, 0, 0, 0, 0]] }, identities: {} },
      "gan",
    );
    expect(snap.trading_pair).toBe("");
    expect(snap.connector).toBe("");
  });
});

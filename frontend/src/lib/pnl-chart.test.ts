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
import {
  HISTORY_POINT_BUDGET,
  POSITION_AXIS_PAD,
  SAMPLING_INTERVALS,
  aggregatePnlSeries,
  pickSamplingInterval,
  positionAreaExtent,
  positionAxisDomain,
  positionQuoteValue,
  samplingIntervalSince,
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
      { time: ms("10:00"), realized: 7, unrealized: 3, total: 10, volume: 100, position: 0 },
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

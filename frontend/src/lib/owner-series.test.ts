/**
 * The claim this chart rests on, pinned.
 *
 * The floor draws one line per agent and one Total, and every one of them is an
 * `aggregatePnlSeries` call — the same call `PerfBrowser` makes for a single
 * `?scope=agent:{runKey}`. The point of the design is that the Total then
 * equals the sum of the owner lines **at every instant** as a property of that
 * fold rather than as a promise, because it forward-fills per controller and
 * summation is linear.
 *
 * A property is only worth having if it is tested where it could break: over
 * staggered start times, where an owner contributes nothing before its first
 * snapshot, and across the live "now" point the fold appends from real time.
 */

import { describe, expect, it } from "vitest";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import {
  FOCUS_RADIUS_PX,
  mergeOwnerRows,
  nearestSeries,
  ownerDataKey,
  ownerSeries,
  parseBaseline,
  parseBasis,
  rebaseRows,
  shortenLabels,
} from "@/lib/owner-series";

function snap(
  bot: string,
  controller: string,
  at: string,
  over: Partial<ControllerPerformanceSnapshot> = {},
): ControllerPerformanceSnapshot {
  return {
    timestamp: at,
    bot_name: bot,
    controller_id: controller,
    controller_name: "pmm_simple",
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

/**
 * Two agents, three controllers, staggered starts.
 *
 * `alpha` runs two controllers from 10:00; `beta` joins at 10:10 with one. The
 * union timeline therefore has an instant at which one owner has no value at
 * all, which is the case a naive merge gets wrong.
 */
const SNAPSHOTS = [
  snap("bot-a", "c1", "2026-09-04T10:00:00Z", { realized_pnl_quote: 10, volume_traded: 100 }),
  snap("bot-a", "c2", "2026-09-04T10:00:00Z", { realized_pnl_quote: 5, volume_traded: 50 }),
  snap("bot-a", "c1", "2026-09-04T10:05:00Z", { realized_pnl_quote: 20, volume_traded: 220 }),
  snap("bot-b", "c3", "2026-09-04T10:10:00Z", {
    realized_pnl_quote: -4,
    unrealized_pnl_quote: 2,
    volume_traded: 40,
  }),
  snap("bot-a", "c2", "2026-09-04T10:15:00Z", {
    realized_pnl_quote: 7,
    unrealized_pnl_quote: -1,
    volume_traded: 90,
  }),
];

const OWNERS = [
  { key: "alpha", label: "Alpha", keys: ["bot-a:c1", "bot-a:c2"] },
  { key: "beta", label: "Beta", keys: ["bot-b:c3"] },
];

function live(bot: string, controller: string, over: Partial<ControllerInfo> = {}) {
  return {
    bot_name: bot,
    controller_id: controller,
    controller_name: "pmm_simple",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    volume_traded: 0,
    positions_summary: [],
    ...over,
  } as unknown as ControllerInfo;
}

describe("the total is the sum of the owners", () => {
  it("holds at every instant, over staggered starts", () => {
    const series = ownerSeries(SNAPSHOTS, OWNERS, []);
    const { rows, keys } = mergeOwnerRows([series.total], series.owners);

    expect(keys).toEqual(["alpha", "beta"]);
    expect(rows.length).toBeGreaterThan(2);
    for (const row of rows) {
      const parts = keys.reduce((sum, key) => {
        const value = row[ownerDataKey(key)];
        return sum + (typeof value === "number" ? value : 0);
      }, 0);
      expect(parts).toBeCloseTo(row.total, 9);
    }
  });

  it("still holds across the live point the fold appends from real time", () => {
    const controllers = [
      live("bot-a", "c1", { realized_pnl_quote: 33, volume_traded: 400 }),
      live("bot-a", "c2", { realized_pnl_quote: 9, volume_traded: 120 }),
      live("bot-b", "c3", { realized_pnl_quote: -2, volume_traded: 70 }),
    ];
    const series = ownerSeries(SNAPSHOTS, OWNERS, controllers);
    const { rows, keys } = mergeOwnerRows([series.total], series.owners);
    const last = rows[rows.length - 1];

    // Every series' live point is snapped to one instant, so the last row is a
    // live row for all three of them rather than a step where the total moved
    // and the owners had not.
    expect(last.total).toBeCloseTo(33 + 9 - 2, 9);
    expect(
      keys.reduce((sum, key) => sum + (last[ownerDataKey(key)] as number), 0),
    ).toBeCloseTo(last.total, 9);
  });

  it("gives an owner no value at all before its first snapshot", () => {
    const series = ownerSeries(SNAPSHOTS, OWNERS, []);
    const { rows } = mergeOwnerRows([series.total], series.owners);
    const first = rows[0];

    // A gap, not a zero: recharts draws nothing, which is the truth. A zero
    // would draw a flat line along the axis for trading that had not begun.
    expect(first[ownerDataKey("alpha")]).toBeCloseTo(15, 9);
    expect(first[ownerDataKey("beta")]).toBeUndefined();
  });
});

describe("the flow and the stock", () => {
  it("forward-fills the stock and charges the flow to its own bucket only", () => {
    const series = ownerSeries(SNAPSHOTS, OWNERS, []);
    const { rows } = mergeOwnerRows([series.total], series.owners);

    // `volume` is cumulative and carries; `volumeDelta` is one bucket's trading
    // and a forward fill would charge it again to every later bucket.
    const totalDelta = rows.reduce((sum, row) => sum + row.volumeDelta, 0);
    const lifetime = rows[rows.length - 1].volume;
    expect(totalDelta).toBeLessThan(lifetime);
    expect(lifetime).toBeGreaterThan(0);
  });
});

describe("the four toggle states", () => {
  const rows = [
    { time: 1, realized: 0, unrealized: 0, total: 10, volume: 0, volumeDelta: 0, position: 0, [ownerDataKey("alpha")]: 6, [ownerDataKey("beta")]: 4 },
    { time: 2, realized: 0, unrealized: 0, total: 30, volume: 0, volumeDelta: 0, position: 0, [ownerDataKey("alpha")]: 20, [ownerDataKey("beta")]: 10 },
  ];
  const keys = ["alpha", "beta"];
  const capital = { total: 1000, alpha: 600, beta: 400 };

  it("absolute from inception is the series untouched", () => {
    const out = rebaseRows(rows, keys, { basis: "abs", from: "inception", capital });
    expect(out.rows).toBe(rows);
    expect(out.unplottable).toEqual([]);
  });

  it("absolute over the window subtracts each series' own first value", () => {
    const out = rebaseRows(rows, keys, { basis: "abs", from: "window", capital });
    expect(out.rows[1].total).toBe(20);
    expect(out.rows[1][ownerDataKey("alpha")]).toBe(14);
    expect(out.rows[1][ownerDataKey("beta")]).toBe(6);
  });

  it("relative divides each series by its own declared capital", () => {
    const out = rebaseRows(rows, keys, { basis: "rel", from: "inception", capital });
    expect(out.rows[1].total).toBeCloseTo(3, 9);
    expect(out.rows[1][ownerDataKey("alpha")]).toBeCloseTo((20 / 600) * 100, 9);
  });

  it("relative over the window does both", () => {
    const out = rebaseRows(rows, keys, { basis: "rel", from: "window", capital });
    expect(out.rows[1][ownerDataKey("beta")]).toBeCloseTo((6 / 400) * 100, 9);
  });

  it("lists an owner with no declared capital and does not plot it", () => {
    const out = rebaseRows(rows, keys, {
      basis: "rel",
      from: "inception",
      capital: { total: 1000, alpha: 600, beta: 0 },
    });
    expect(out.unplottable).toEqual([{ key: "beta", reason: "no declared capital" }]);
    for (const row of out.rows) {
      expect(row[ownerDataKey("beta")]).toBeUndefined();
      // No Infinity, no NaN, and no silent 0% either.
      expect(Number.isFinite(row[ownerDataKey("alpha")])).toBe(true);
    }
  });

  it("says nothing about capital while the basis is absolute", () => {
    const out = rebaseRows(rows, keys, {
      basis: "abs",
      from: "inception",
      capital: { total: 0, alpha: 0, beta: 0 },
    });
    expect(out.unplottable).toEqual([]);
  });
});

describe("the URL parsers", () => {
  it("fall back to their default rather than throwing", () => {
    expect(parseBasis("rel")).toBe("rel");
    expect(parseBasis("nonsense")).toBe("abs");
    expect(parseBasis(null)).toBe("abs");
    expect(parseBaseline("window")).toBe("window");
    expect(parseBaseline("nonsense")).toBe("inception");
  });
});

describe("a floor spanning two servers", () => {
  it("sums an owner's repeated key rather than letting one server win", () => {
    const a = ownerSeries(
      [snap("bot-a", "c1", "2026-09-04T10:00:00Z", { realized_pnl_quote: 10 })],
      [{ key: "alpha", label: "Alpha", keys: ["bot-a:c1"] }],
      [],
    );
    const b = ownerSeries(
      [snap("bot-z", "c9", "2026-09-04T10:00:00Z", { realized_pnl_quote: 4 })],
      [{ key: "alpha", label: "Alpha", keys: ["bot-z:c9"] }],
      [],
    );
    const { rows, keys } = mergeOwnerRows(
      [a.total, b.total],
      [...a.owners, ...b.owners],
    );
    expect(keys).toEqual(["alpha"]);
    expect(rows[rows.length - 1][ownerDataKey("alpha")]).toBeCloseTo(14, 9);
    expect(rows[rows.length - 1].total).toBeCloseTo(14, 9);
  });
});

// ── Which line is the cursor on (FEAT-117) ──
//
// A fleet of eighteen controllers draws eighteen lines, and the tooltip used to
// list every one of them at the hovered instant with nothing saying which one
// the reader was pointing at. The pick is made in pixels because the two bases
// this chart draws — absolute quote and percent of capital — have y-axes three
// orders of magnitude apart, and any threshold in the data's own units is right
// on one of them and meaningless on the other.

describe("nearestSeries", () => {
  /** A y-axis where one unit of value is one pixel, counting down from 500. */
  const scale = (value: number) => 500 - value;
  const values = new Map([
    ["total", 100],
    ["alpha", 60],
    ["beta", 20],
  ]);

  it("picks the line under the cursor", () => {
    // 500 - 60 = 440 is alpha's pixel; two off it is still alpha.
    expect(nearestSeries(values, 442, scale)).toBe("alpha");
    expect(nearestSeries(values, 400, scale)).toBe("total");
    expect(nearestSeries(values, 480, scale)).toBe("beta");
  });

  it("picks nothing when the cursor is on nothing", () => {
    // Halfway between beta (480) and alpha (440) is outside both radii.
    expect(nearestSeries(values, 460, scale)).toBeNull();
    expect(nearestSeries(values, 10, scale)).toBeNull();
  });

  it("measures in pixels, so both bases behave the same", () => {
    // The same picture drawn as percent: values a hundredth the size, and a
    // scale a hundred times steeper. The answer must not change.
    const percent = new Map([["alpha", 0.6], ["beta", 0.2]]);
    const steep = (value: number) => 500 - value * 100;
    expect(nearestSeries(percent, 442, steep)).toBe("alpha");
    expect(nearestSeries(percent, 460, steep)).toBeNull();
  });

  it("has no answer before the chart has measured itself", () => {
    expect(nearestSeries(values, 440, null)).toBeNull();
    expect(nearestSeries(values, undefined, scale)).toBeNull();
    expect(nearestSeries(values, Number.NaN, scale)).toBeNull();
  });

  it("skips a series the scale cannot place, rather than picking it", () => {
    const gappy = new Map([["alpha", Number.NaN], ["beta", 20]]);
    expect(nearestSeries(gappy, 480, scale)).toBe("beta");
    expect(nearestSeries(gappy, 440, scale)).toBeNull();
  });

  it("breaks a tie toward the first entry, which is the legend's order", () => {
    const tied = new Map([["alpha", 60], ["beta", 60]]);
    expect(nearestSeries(tied, 440, scale)).toBe("alpha");
  });

  it("states its radius, so the tooltip and the plot agree on 'on a line'", () => {
    expect(nearestSeries(values, 440 + FOCUS_RADIUS_PX - 1, scale)).toBe("alpha");
    expect(nearestSeries(values, 440 + FOCUS_RADIUS_PX, scale)).toBeNull();
  });
});

describe("shortenLabels", () => {
  it("prints only what differs between siblings named by convention", () => {
    const { stem, short } = shortenLabels([
      "pmm_king_btc_brl",
      "pmm_king_btc_brl_v1",
      "pmm_king_btc_brl_b_v2",
    ]);
    expect(stem).toBe("pmm_king_btc");
    expect(short).toEqual(["brl", "brl_v1", "brl_b_v2"]);
  });

  it("never leaves the shortest sibling without a name of its own", () => {
    const { short } = shortenLabels(["alpha_beta", "alpha_beta_v1"]);
    expect(short).toEqual(["beta", "beta_v1"]);
  });

  it("cuts on token boundaries, never mid-word", () => {
    // The two share the characters `market_mak`; only `market` is a token.
    const { stem, short } = shortenLabels(["market_making_sol", "market_makers_sol"]);
    expect(stem).toBe("market");
    expect(short).toEqual(["making_sol", "makers_sol"]);
  });

  it("leaves labels alone when there is nothing worth taking off", () => {
    expect(shortenLabels(["sol_usdc", "btc_usdt"])).toEqual({
      stem: "",
      short: ["sol_usdc", "btc_usdt"],
    });
    expect(shortenLabels(["only_one"])).toEqual({ stem: "", short: ["only_one"] });
  });

  it("reads a dash-separated set the same way", () => {
    const { stem, short } = shortenLabels(["hedge-bot-a", "hedge-bot-b"]);
    expect(stem).toBe("hedge-bot");
    expect(short).toEqual(["a", "b"]);
  });
});

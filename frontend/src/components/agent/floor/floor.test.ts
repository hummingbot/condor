/**
 * The floor's one claim: the parts add up to the whole.
 *
 * Everything else on the page is a presentation of a fold that already existed.
 * What is new is the *partition* — that the fleet total equals the agent rows
 * plus the two unowned buckets plus whatever is attributed to a run key no
 * listed agent claims — and that every sub-fold is a slice of the same spine.
 * Those are the properties pinned here, together with the two refusals that
 * make the strip honest: a reading with a zero denominator is suppressed rather
 * than zeroed, and the floor's row is the *agent entire* rather than one scoped
 * strategy of it.
 */

import { describe, expect, it } from "vitest";

import {
  feeBps,
  groupSpine,
  mergeSlices,
  partitionFloor,
  sumTotals,
  turnover,
  type FloorSlice,
} from "@/components/agent/floor/floor";
import {
  floorTargets,
  foldRows,
  foldTargets,
} from "@/components/agent/workspace/fleet";
import { BEFORE_LEDGER, OUTSIDE } from "@/components/perf/agentFilter";
import type { AgentSummary, ControllerInfo } from "@/lib/api";
import type { ConvertQuote, PerfLeaf } from "@/lib/perf-tree";
import { foldLeaves } from "@/lib/perf-tree";

const NOW = Date.parse("2026-09-04T12:00:00Z");
const cv: ConvertQuote = (value) => value;

let seq = 0;

/** One live controller leaf, in the shape `runningLeaves` produces. */
function leaf(over: Partial<PerfLeaf> = {}): PerfLeaf {
  seq += 1;
  const bot = over.bot ?? `bot-${seq}`;
  const controllerId = over.controllerId ?? `c${seq}`;
  return {
    id: `${bot}:${controllerId}`,
    kind: "controller",
    label: controllerId,
    bot,
    agent: "",
    how: "none",
    controllerId,
    executorType: "pmm_simple",
    connector: "binance",
    pair: "SOL-USDC",
    realized: 0,
    unrealized: 0,
    net: 0,
    volume: 0,
    fees: 0,
    capital: 0,
    closeTypes: {},
    positions: [],
    startedAt: NOW - 3_600_000,
    endedAt: null,
    running: true,
    status: "running",
    source: {} as ControllerInfo,
    ...over,
  };
}

/** A deed index whose ledger opened an hour ago, so `agentBucket` can judge. */
const DEEDS = { bots: {}, since: (NOW - 7_200_000) / 1000 } as never;

function position(amount: number, price: number, side = "buy") {
  return { amount, entry_price: price, side };
}

describe("the partition is complete", () => {
  const leaves = [
    leaf({ agent: "alpha.mm", how: "namespace", net: 100, volume: 1_000 }),
    leaf({ agent: "alpha.chat", how: "deed", net: 5, volume: 20 }),
    leaf({ agent: "beta.grid", how: "namespace", net: -30, volume: 500 }),
    // Attributed to an agent nobody listed — the residual by construction: a
    // run key whose agent was deleted while its bots went on trading.
    leaf({ agent: "ghost.mm", how: "namespace", net: 7, volume: 70 }),
    // Unowned, after the ledger opened — Outside Condor.
    leaf({ net: 11, volume: 90, startedAt: NOW - 60_000 }),
    // Unowned, before the ledger opened — Before the ledger.
    leaf({ net: -2, volume: 30, startedAt: NOW - 9_000_000 }),
  ];

  const listed = ["alpha", "beta"];
  const partition = partitionFloor({ leaves, deeds: DEEDS, convert: cv, now: NOW, listed });
  const rows = foldRows(
    listed.map((slug) => ({ slug, strategy: null })),
    { leaves, deeds: DEEDS, convert: cv, now: NOW, symbol: "$" },
  );

  it("names the two unowned buckets and the unclaimed run key", () => {
    expect(partition.others.map((o) => o.kind).sort()).toEqual([
      "before",
      "outside",
      "residual",
    ]);
    const residual = partition.others.find((o) => o.kind === "residual")!;
    expect(residual.key).toBe("ghost.mm");
    expect(residual.scope).toBe("agent:ghost.mm");
    expect(residual.totals.net).toBe(7);
  });

  it("keeps the unowned buckets apart rather than lumping them together", () => {
    const outside = partition.others.find((o) => o.key === OUTSIDE)!;
    const before = partition.others.find((o) => o.key === BEFORE_LEDGER)!;
    expect(outside.totals.net).toBe(11);
    expect(before.totals.net).toBe(-2);
  });

  it("makes the fleet total the sum of every part, exactly", () => {
    const fromRows = [...rows.values()].reduce((sum, fold) => sum + fold.net, 0);
    const fromOthers = partition.others.reduce((sum, o) => sum + o.totals.net, 0);
    expect(fromRows + fromOthers).toBeCloseTo(partition.total.net, 9);
    // And every leaf is in exactly one of them.
    expect(partition.total.count).toBe(leaves.length);
  });

  it("reports it through mergeSlices as a zero residual", () => {
    const slice: FloorSlice = {
      server: "s1",
      symbol: "$",
      rows,
      others: partition.others,
      total: partition.total,
      byPair: [],
      byVenue: [],
      series: { total: [], owners: [] },
    };
    const model = mergeSlices([slice], [
      { slug: "alpha", name: "Alpha" },
      { slug: "beta", name: "Beta" },
    ]);
    expect(model.unaccounted).toBeCloseTo(0, 9);
    expect(model.rows.map((r) => r.slug)).toEqual(["alpha", "beta"]);
    expect(model.total.net).toBeCloseTo(91, 9);
  });

  it("claims an agent's pseudo runs for its own row, not for the residual", () => {
    // `alpha.chat` is a chat deploy: it belongs to alpha as much as its
    // strategy's records do, and `reconcile` includes it unconditionally.
    expect(rows.get("alpha")!.net).toBe(105);
    expect(partition.others.some((o) => o.key.startsWith("alpha."))).toBe(false);
  });
});

describe("the breakdowns are slices of the same spine", () => {
  const leaves = [
    leaf({ agent: "alpha.mm", pair: "SOL-USDC", connector: "binance", net: 40, volume: 400 }),
    leaf({ agent: "alpha.mm", pair: "BTC-USDT", connector: "binance", net: -10, volume: 900 }),
    leaf({ agent: "beta.grid", pair: "SOL-USDC", connector: "kucoin", net: 6, volume: 70 }),
  ];
  const partition = partitionFloor({
    leaves,
    deeds: DEEDS,
    convert: cv,
    now: NOW,
    listed: ["alpha", "beta"],
  });

  it("sums the per-instrument folds to the fleet fold", () => {
    const byPair = groupSpine(partition.spine, (l) => l.pair, cv, NOW);
    expect(byPair.map((b) => b.key).sort()).toEqual(["BTC-USDT", "SOL-USDC"]);
    expect(byPair.reduce((sum, b) => sum + b.totals.net, 0)).toBeCloseTo(
      partition.total.net,
      9,
    );
    expect(byPair.reduce((sum, b) => sum + b.totals.volume, 0)).toBeCloseTo(
      partition.total.volume,
      9,
    );
  });

  it("sums the per-venue folds to the fleet fold", () => {
    const byVenue = groupSpine(partition.spine, (l) => l.connector, cv, NOW);
    expect(byVenue.map((b) => b.key).sort()).toEqual(["binance", "kucoin"]);
    expect(byVenue.reduce((sum, b) => sum + b.totals.net, 0)).toBeCloseTo(
      partition.total.net,
      9,
    );
  });

  it("reads signed exposure off the positions, not off a side field", () => {
    const short = [
      leaf({ agent: "alpha.mm", pair: "SOL-USDC", positions: [position(2, 100, "SELL")] }),
      leaf({ agent: "alpha.mm", pair: "SOL-USDC", positions: [position(1, 100, "BUY")] }),
    ];
    const buckets = groupSpine(short, (l) => l.pair, cv, NOW);
    expect(buckets[0].exposure).toBeCloseTo(-100, 9);
  });
});

describe("sumTotals is a rule, not a spread", () => {
  const a = foldLeaves(
    [leaf({ net: 10, volume: 100, running: false, endedAt: NOW, startedAt: NOW - 3_600_000 })],
    cv,
    NOW,
  );
  const b = foldLeaves(
    [
      leaf({ net: -4, volume: 40, running: false, endedAt: NOW, startedAt: NOW - 7_200_000 }),
      leaf({ net: 1, volume: 10, running: false, endedAt: NOW, startedAt: NOW - 7_200_000 }),
    ],
    cv,
    NOW,
  );

  it("adds the additive fields", () => {
    const out = sumTotals([a, b]);
    expect(out.net).toBeCloseTo(7, 9);
    expect(out.volume).toBeCloseTo(150, 9);
    expect(out.count).toBe(3);
  });

  it("recomputes the win rate instead of averaging two of them", () => {
    // One win of one, plus one win of two — 2/3, not the 75% an average gives.
    const out = sumTotals([a, b]);
    expect(out.closed).toBe(3);
    expect(out.wins).toBe(2);
    expect(out.winRate).toBeCloseTo(2 / 3, 9);
  });

  it("takes the max runtime, because two fleets ran one afternoon", () => {
    expect(sumTotals([a, b]).hours).toBeCloseTo(2, 9);
  });

  it("drops the per-leaf return rather than reporting one nobody earned", () => {
    const one = foldLeaves([leaf({ returnPct: 12 })], cv, NOW);
    expect(one.returnPct).toBe(12);
    expect(sumTotals([one, one]).returnPct).toBeUndefined();
  });
});

describe("the normalized readings", () => {
  it("are suppressed, not zeroed, when their denominator is zero", () => {
    const nothing = foldLeaves([leaf({})], cv, NOW);
    expect(feeBps(nothing)).toBeNull();
    expect(turnover(nothing)).toBeNull();
  });

  it("measure fees as bps of volume and volume as turnover of capital", () => {
    const traded = foldLeaves(
      [leaf({ volume: 1_000_000, fees: 250, capital: 20_000 })],
      cv,
      NOW,
    );
    expect(feeBps(traded)).toBeCloseTo(2.5, 9);
    expect(turnover(traded)).toBeCloseTo(50, 9);
  });
});

describe("the floor's row is the agent entire", () => {
  function strategy(slug: string, server = "") {
    return {
      slug,
      name: slug.toUpperCase(),
      session_count: 1,
      instances: [],
      server_name: server,
    } as unknown as AgentSummary["strategies"][number];
  }

  function agent(over: Partial<AgentSummary> = {}): AgentSummary {
    return {
      slug: "alpha",
      name: "Alpha",
      status: "idle",
      session_count: 1,
      total_pnl: 0,
      total_volume: 0,
      open_positions: 0,
      server_name: "s1",
      strategies: [strategy("mm")],
      instances: [],
      ...over,
    } as AgentSummary;
  }

  it("prints the same number as the home's row when one strategy is in scope", () => {
    const leaves = [leaf({ agent: "alpha.mm", how: "namespace", net: 64.12, volume: 2_549 })];
    const input = { leaves, deeds: DEEDS, convert: cv, now: NOW, symbol: "$" };
    const home = foldRows(foldTargets([agent()], null)[0].targets, input);
    const floor = foldRows(floorTargets([agent()], null)[0].targets, input);

    expect(home.get("alpha")!.net).toBe(64.12);
    expect(floor.get("alpha")!.net).toBe(home.get("alpha")!.net);
  });

  it("keeps the other strategies' records that the home's row narrows away", () => {
    const two = agent({ strategies: [strategy("mm"), strategy("grid")] });
    const leaves = [
      leaf({ agent: "alpha.mm", how: "namespace", net: 100, volume: 10 }),
      leaf({ agent: "alpha.grid", how: "namespace", net: 25, volume: 5 }),
    ];
    const input = { leaves, deeds: DEEDS, convert: cv, now: NOW, symbol: "$" };

    expect(foldRows(foldTargets([two], null)[0].targets, input).get("alpha")!.net).toBe(100);
    // The whole reason `floorTargets` exists: `alpha.grid` is attributed, so it
    // is in neither unowned bucket, and a row that narrowed it away would drop
    // it out of a total whose only job is to be complete.
    expect(floorTargets([two], null)[0].targets[0].strategy).toBeNull();
    expect(foldRows(floorTargets([two], null)[0].targets, input).get("alpha")!.net).toBe(125);
  });

  it("lists an agent on every server any of its strategies declares", () => {
    const spread = agent({
      strategies: [strategy("mm", "s1"), strategy("grid", "s2")],
    });
    expect(floorTargets([spread], null).map((g) => g.server)).toEqual(["s1", "s2"]);
  });

  it("carries the spine's derived readings out of the one fold", () => {
    const leaves = [
      leaf({
        agent: "alpha.mm",
        how: "namespace",
        bot: "bot-x",
        controllerId: "c9",
        net: 3,
        positions: [position(2, 50, "SELL")],
      }),
      leaf({
        agent: "alpha.mm",
        how: "namespace",
        running: false,
        endedAt: NOW - 60_000,
      }),
    ];
    const fold = foldRows([{ slug: "alpha", strategy: null }], {
      leaves,
      deeds: DEEDS,
      convert: cv,
      now: NOW,
      symbol: "$",
    }).get("alpha")!;

    expect(fold.keys).toContain("bot-x:c9");
    expect(fold.exposure).toBeCloseTo(-100, 9);
    expect(fold.lastClose).toBe(NOW - 60_000);
    expect(fold.running).toBe(1);
    expect(fold.totals.net).toBe(3);
  });
});

describe("mergeSlices across servers", () => {
  it("sums an agent's per-server folds into one row", () => {
    const make = (net: number): FloorSlice => ({
      server: `s${net}`,
      symbol: "$",
      rows: new Map([
        [
          "alpha",
          {
            net,
            volume: net * 10,
            symbol: "$",
            reported: true,
            totals: foldLeaves([leaf({ net, volume: net * 10 })], cv, NOW),
            keys: [`bot:${net}`],
            exposure: net,
            lastClose: net,
            running: 1,
          },
        ],
      ]),
      others: [],
      total: foldLeaves([leaf({ net, volume: net * 10 })], cv, NOW),
      byPair: [],
      byVenue: [],
      series: { total: [], owners: [] },
    });

    const model = mergeSlices([make(10), make(4)], [{ slug: "alpha", name: "Alpha" }]);
    expect(model.rows).toHaveLength(1);
    expect(model.rows[0].totals.net).toBeCloseTo(14, 9);
    expect(model.rows[0].keys).toEqual(["bot:10", "bot:4"]);
    expect(model.rows[0].exposure).toBeCloseTo(14, 9);
    expect(model.rows[0].lastClose).toBe(10);
    expect(model.unaccounted).toBeCloseTo(0, 9);
  });

  it("drops an agent no server has answered for rather than showing it a zero", () => {
    const model = mergeSlices([], [{ slug: "alpha", name: "Alpha" }]);
    expect(model.rows).toEqual([]);
    expect(model.total.net).toBe(0);
  });
});

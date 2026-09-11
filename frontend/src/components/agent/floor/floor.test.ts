/**
 * The one claim under every reading of a spine: the parts add up to the whole.
 *
 * The floor is not a page any more (FEAT-116) — the browser draws the report
 * and `components/perf/scopeOwners` decides what it splits into — but the
 * arithmetic it needed did not go with it, because it was never about the page.
 * What is pinned here is that a breakdown is a *slice of the same spine* and so
 * sums to the same fold; that {@link sumTotals} is a rule and not a spread,
 * because three of its fields are not additive; and that an agent's fold is the
 * *agent entire* rather than one scoped strategy of it, which is what
 * `floorTargets` exists to say.
 */

import { describe, expect, it } from "vitest";

import { groupSpine, sumTotals } from "@/components/agent/floor/floor";
import {
  floorTargets,
  foldRows,
  foldTargets,
} from "@/components/agent/workspace/fleet";
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

describe("the breakdowns are slices of the same spine", () => {
  const leaves = [
    leaf({ agent: "alpha.mm", pair: "SOL-USDC", connector: "binance", net: 40, volume: 400 }),
    leaf({ agent: "alpha.mm", pair: "BTC-USDT", connector: "binance", net: -10, volume: 900 }),
    leaf({ agent: "beta.grid", pair: "SOL-USDC", connector: "kucoin", net: 6, volume: 70 }),
  ];
  // The scope's own accounting spine and its own fold — the two things the
  // browser hands the band and the KPI tiles, from one `scope.leaves`.
  const whole = foldLeaves(leaves, cv, NOW);

  it("sums the per-instrument folds to the scope's own fold", () => {
    const byPair = groupSpine(leaves, (l) => l.pair, cv, NOW);
    expect(byPair.map((b) => b.key).sort()).toEqual(["BTC-USDT", "SOL-USDC"]);
    expect(byPair.reduce((sum, b) => sum + b.totals.net, 0)).toBeCloseTo(whole.net, 9);
    expect(byPair.reduce((sum, b) => sum + b.totals.volume, 0)).toBeCloseTo(
      whole.volume,
      9,
    );
  });

  it("sums the per-venue folds to the scope's own fold", () => {
    const byVenue = groupSpine(leaves, (l) => l.connector, cv, NOW);
    expect(byVenue.map((b) => b.key).sort()).toEqual(["binance", "kucoin"]);
    expect(byVenue.reduce((sum, b) => sum + b.totals.net, 0)).toBeCloseTo(whole.net, 9);
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

describe("an agent's fold is the agent entire", () => {
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

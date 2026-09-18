/**
 * The one claim under every reading of a spine: the parts add up to the whole.
 *
 * The floor is not a page any more (FEAT-116) — the browser draws the report
 * and `components/perf/scopeOwners` decides what it splits into — but the
 * arithmetic it needed did not go with it, because it was never about the page.
 * What is pinned here is that a breakdown is a *slice of the same spine* and so
 * sums to the same fold; that {@link sumTotals} is a rule and not a spread,
 * because three of its fields are not additive.
 */

import { describe, expect, it } from "vitest";

import { groupSpine, sumTotals } from "@/components/agent/floor/floor";
import type { ControllerInfo } from "@/lib/api";
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

// ── Reading a spine, and cutting one across the tree (FEAT-112, FEAT-116) ──
//
// The floor was a page: every agent's trading added up, with a strip, a chart
// with one line per agent, and two breakdowns that cut across the whole fleet.
// It is not a page any more. Every one of those is a reading of a scope the
// browser behind `/bots` already folds — the strip is its KPI tiles, the chart
// is that report split by the level below the scope you are on
// (`components/perf/scopeOwners`), and the breakdowns are the band's third
// entry (`components/perf/ScopeBreakdowns`).
//
// What survives here is what those readings are made of, and it is the part
// that was never about the page:
//
//  - {@link readSpine} — how every part of that report reads one accounting
//    spine, so that a fold, an exposure and a set of chart keys come out of one
//    pass and cannot disagree.
//  - {@link groupSpine} — the spine cut by a field of the leaf rather than by a
//    level of the tree, which is what "by instrument" and "by venue" are.
//  - {@link sumTotals} — two folds of two disjoint sets of records as one fold,
//    as a named rule rather than a spread, because three of the fields are not
//    additive.
//
// The invariant every one of them rests on belongs to the tree and not to this
// file: a node's `leaves` is its **accounting spine**, not its whole subtree —
// a live controller stands in for its executors — so a scope's children
// partition it, disjointly and completely, and a sub-fold sliced off that one
// array with that one `ConvertQuote` sums to the whole by construction rather
// than by assertion. A breakdown folded with a different converter, or over a
// different selection of leaves, stops summing to anything.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import {
  spineExposure,
  spineKeys,
  spineLastClose,
} from "@/components/agent/workspace/fleet";
import {
  UNKNOWN_LABEL,
  foldLeaves,
  type ConvertQuote,
  type PerfLeaf,
  type PerfTotals,
} from "@/lib/perf-tree";

/** An empty fold — the identity of {@link sumTotals}. */
export function emptyTotals(): PerfTotals {
  return {
    realized: 0,
    unrealized: 0,
    net: 0,
    volume: 0,
    fees: 0,
    capital: 0,
    positions: 0,
    bots: 0,
    count: 0,
    closed: 0,
    wins: 0,
    winRate: undefined,
    hours: 0,
    closeTypes: [],
    closeTotal: 0,
    returnPct: undefined,
  };
}

/**
 * Two folds of two disjoint sets of records, as one fold — a named rule.
 *
 * Not a spread, and not a loop at the call site. Three of these fields are not
 * additive and getting any of them wrong is a number nobody earned:
 *
 *  - **`winRate` is recomputed** from the summed `wins` and `closed`. Averaging
 *    two rates weights a scope of three closes the same as one of three hundred.
 *  - **`hours` takes the max.** It is *measured elapsed time*, so two fleets
 *    that ran the same afternoon ran for one afternoon, not two.
 *  - **`returnPct` is dropped.** `perf-tree.ts` is explicit that a per-leaf
 *    return summed across a scope "reports a return nobody earned", and that
 *    rule does not stop applying because the scope got bigger.
 *
 * `bots` is summed, which is right across servers — the same bot name on two
 * servers is two bots — and right within one, where the parts are disjoint.
 */
export function sumTotals(parts: readonly PerfTotals[]): PerfTotals {
  const out = emptyTotals();
  const merged = new Map<string, number>();
  for (const part of parts) {
    out.realized += part.realized;
    out.unrealized += part.unrealized;
    out.net += part.net;
    out.volume += part.volume;
    out.fees += part.fees;
    out.capital += part.capital;
    out.positions += part.positions;
    out.bots += part.bots;
    out.count += part.count;
    out.closed += part.closed;
    out.wins += part.wins;
    out.closeTotal += part.closeTotal;
    out.hours = Math.max(out.hours, part.hours);
    for (const [type, count] of part.closeTypes) {
      merged.set(type, (merged.get(type) ?? 0) + count);
    }
  }
  out.winRate = out.closed > 0 ? out.wins / out.closed : undefined;
  out.closeTypes = [...merged].sort((a, b) => b[1] - a[1]);
  return out;
}

/** Everything a reading of one spine prints, whoever it is about. */
export interface FloorMoney {
  totals: PerfTotals;
  /** Signed quote notional over the spine, in display currency. */
  exposure: number;
  /** `max(endedAt)` over the spine, or `null`. */
  lastClose: number | null;
  /** The spine's controller keys — what this part's chart line is drawn from. */
  keys: string[];
  /** Leaves still live. */
  running: number;
}

/** Reading a spine the way every part of the report reads one. */
export function readSpine(
  spine: PerfLeaf[],
  convert: ConvertQuote,
  now: number,
): FloorMoney {
  return {
    totals: foldLeaves(spine, convert, now),
    exposure: spineExposure(spine, convert),
    lastClose: spineLastClose(spine),
    keys: spineKeys(spine),
    running: spine.filter((leaf) => leaf.running).length,
  };
}

/** One slice of a breakdown — a bucket of the same spine, folded. */
export interface FloorBucket extends FloorMoney {
  key: string;
  label: string;
}

/**
 * The spine cut by one field of the leaf, each bucket folded the same way.
 *
 * Deliberately not a fifth `GroupAxis`. `leaf.connector` is on the leaf but is
 * not an axis, and adding one means edits to `keyFor`, `AXIS_PREFIX`,
 * `AXIS_KIND`, `AXIS_UNIQUE` and `GROUP_AXES`, plus a URL grammar and a picker
 * preset on `/bots`. The breakdowns want a flat cut, not a tree level, so they
 * group locally and leave the axis vocabulary alone until something actually
 * wants to nest by venue.
 *
 * Because every bucket is a slice of the one spine folded with the one
 * converter, `Σ buckets == the scope's own fold` by construction rather than by
 * assertion.
 *
 * Emitted in the spine's own order — the order the records arrived in. Ranking
 * is a reading order and belongs to whatever draws them.
 */
export function groupSpine(
  spine: readonly PerfLeaf[],
  of: (leaf: PerfLeaf) => string,
  convert: ConvertQuote,
  now: number,
): FloorBucket[] {
  const by = new Map<string, PerfLeaf[]>();
  for (const leaf of spine) {
    const key = of(leaf) || UNKNOWN_LABEL;
    const held = by.get(key);
    if (held) held.push(leaf);
    else by.set(key, [leaf]);
  }
  return [...by].map(([key, held]) => ({
    key,
    label: key,
    ...readSpine(held, convert, now),
  }));
}

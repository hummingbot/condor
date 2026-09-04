// ── The whole fleet, added up once and read four ways (FEAT-112) ──
//
// The floor asks the question the home cannot: not *what is each agent doing*
// but **what do they add up to**. That is a different shape of answer — a chart
// with one line per agent, a strip over the whole population, and two
// breakdowns that cut across every agent at once — and it is a third surface
// over records the repo already folds twice.
//
// It gets exactly one shot at not becoming the third number, and the mechanism
// is not a promise this file makes. It is a property of the tree:
//
//  - `buildTree(leaves, "All", { grouping: DEFAULT_GROUPING, deeds })` groups on
//    `agent` first, and `keyFor` **never returns `""`** for that axis — an
//    unowned leaf is bucketed by `agentBucket` into *Outside Condor* or
//    *Before the ledger* rather than dropped.
//  - A node's `leaves` is its **accounting spine**, not its whole subtree: a
//    live controller stands in for its executors, so nothing double counts.
//
// Therefore the `agent:` nodes' spines partition the root's spine, disjointly
// and completely, and the fleet total *is* the sum of the agent rows plus the
// two named unowned buckets plus whatever is attributed to a run key no listed
// agent claims. That last term is the {@link FloorOther} of kind `residual`,
// and it is shown as its own named row with a lead into its records rather than
// swept into an "other" — the discipline `reconcile` already established, for
// the reason it established it: a term nobody can open is not a term.
//
// Every sub-fold below is sliced off that same spine array and handed the same
// `ConvertQuote`, because `foldLeaves` converts **per leaf** using the leaf's
// own quote. A breakdown folded with a different converter, or over a different
// selection of leaves, stops summing to the whole.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import {
  BEFORE_LEDGER,
  BEFORE_LEDGER_LABEL,
  OUTSIDE,
  OUTSIDE_LABEL,
} from "@/components/perf/agentFilter";
import {
  spineExposure,
  spineKeys,
  spineLastClose,
  type RowFold,
} from "@/components/agent/workspace/fleet";
import {
  agentScope,
  isPseudoRunKey,
  splitRunKey,
} from "@/components/agent/workspace/reconcile";
import { runKeyLabel, type DeedIndex } from "@/lib/agent-attribution";
import type { OwnerSeries } from "@/lib/owner-series";
import type { PnlChartPoint } from "@/lib/pnl-chart";
import {
  AXIS_PREFIX,
  DEFAULT_GROUPING,
  UNKNOWN_LABEL,
  buildTree,
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

/** Everything a floor row prints, whoever it is about. */
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

/** Reading a spine the way every part of this page reads one. */
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

/** A named part of the fleet that is not one of the listed agents. */
export interface FloorOther extends FloorMoney {
  /** Stable across polls and across servers — what the merge keys on. */
  key: string;
  kind: "outside" | "before" | "residual";
  label: string;
  /** The fleet scope that opens exactly these records. */
  scope: string;
}

/** One server's spine, cut every way the page reads it. */
export interface FloorPartition {
  /** Every leaf on this server, folded — what the parts must add up to. */
  total: PerfTotals;
  /** The root's accounting spine, for the breakdowns. */
  spine: PerfLeaf[];
  /** The two unowned buckets and every unclaimed run key, each named. */
  others: FloorOther[];
}

/**
 * The complete partition of one server's records (FEAT-112).
 *
 * `listed` is the set of agent slugs that have a **row** on this server — the
 * targets `floorTargets` emitted for it. Anything attributed to a run key
 * outside that set is the residual, and it is real: a run key can name an agent
 * that has been deleted while its bots go on trading, so its leaves are
 * attributed (hence in neither unowned bucket) and no row claims them. Left
 * unnamed they would drain out of a total whose only job is to be complete.
 */
export function partitionFloor({
  leaves,
  deeds,
  convert,
  now,
  listed,
}: {
  leaves: PerfLeaf[];
  deeds: DeedIndex | null;
  convert: ConvertQuote;
  now: number;
  listed: readonly string[];
}): FloorPartition {
  const tree = buildTree(leaves, "All", { grouping: DEFAULT_GROUPING, deeds });
  const claimed = new Set(listed);
  const others: FloorOther[] = [];

  for (const child of tree.children) {
    if (child.kind !== "agent") continue;
    const bucket = child.id.slice(AXIS_PREFIX.agent.length);
    const money = readSpine(child.leaves, convert, now);
    if (bucket === OUTSIDE || bucket === BEFORE_LEDGER) {
      others.push({
        ...money,
        key: bucket,
        kind: bucket === OUTSIDE ? "outside" : "before",
        label: bucket === OUTSIDE ? OUTSIDE_LABEL : BEFORE_LEDGER_LABEL,
        scope: agentScope(bucket),
      });
      continue;
    }
    // An attributed run key. A pseudo key (`chat` / `delegation` / `ui`) belongs
    // to its agent as much as a strategy's does — `reconcile` includes all three
    // unconditionally — so it is only a residual when its *agent* has no row.
    if (claimed.has(splitRunKey(bucket).agent)) continue;
    others.push({
      ...money,
      key: bucket,
      kind: "residual",
      label: isPseudoRunKey(bucket)
        ? `${runKeyLabel(bucket)} — no agent listed`
        : `${runKeyLabel(bucket)} — claimed by no listed agent`,
      scope: agentScope(bucket),
    });
  }

  return {
    total: foldLeaves(tree.leaves, convert, now),
    spine: tree.leaves,
    others,
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
 * preset on `/bots`. The floor needs a flat breakdown, not a tree level, so it
 * groups locally and leaves the axis vocabulary alone until something actually
 * wants to nest by venue.
 *
 * Because every bucket is a slice of the one spine folded with the one
 * converter, `Σ buckets == the fleet fold` by construction rather than by
 * assertion.
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

/** What one `ServerFloor` publishes upward — totals, never raw leaves. */
export interface FloorSlice {
  server: string;
  /** The display currency's symbol, so every screen prints one currency. */
  symbol: string;
  /** Per listed agent slug, from `foldRows` — the one producer (ARCH-324). */
  rows: ReadonlyMap<string, RowFold>;
  others: FloorOther[];
  total: PerfTotals;
  byPair: FloorBucket[];
  byVenue: FloorBucket[];
  /**
   * One line per part of this server's partition, plus this server's own total.
   *
   * Published as points rather than as leaves for the same reason the folds are
   * published as totals: the conversion into display currency belongs to the
   * server that fetched the records, so the parent merges series that are
   * already in one currency (see {@link mergeSlices}).
   */
  series: { total: PnlChartPoint[]; owners: OwnerSeries[] };
}

/** One agent, as the floor reports it — the agent entire, over every server. */
export interface FloorRow extends FloorMoney {
  slug: string;
  name: string;
  /** Whether its records say anything at all — `reconcile`'s own judgement. */
  reported: boolean;
}

/** The whole floor, merged from every server's slice. */
export interface FloorModel {
  symbol: string;
  rows: FloorRow[];
  others: FloorOther[];
  total: PerfTotals;
  byPair: FloorBucket[];
  byVenue: FloorBucket[];
  /** `total.net − Σ(rows) − Σ(others)`. Zero by construction; shown if not. */
  unaccounted: number;
}

function mergeMoney(parts: readonly FloorMoney[]): FloorMoney {
  return {
    totals: sumTotals(parts.map((p) => p.totals)),
    exposure: parts.reduce((sum, p) => sum + p.exposure, 0),
    lastClose: parts.reduce<number | null>(
      (last, p) =>
        p.lastClose !== null && (last === null || p.lastClose > last) ? p.lastClose : last,
      null,
    ),
    keys: parts.flatMap((p) => p.keys),
    running: parts.reduce((sum, p) => sum + p.running, 0),
  };
}

/**
 * Every server's slice, as one floor.
 *
 * **Currency is why the slices carry totals rather than leaves.** Each server
 * has its own `useRates` converter and `foldLeaves` converts per leaf, so a
 * parent that re-folded merged raw leaves with one converter would change the
 * numbers. Each slice folds itself into display currency and the parent sums,
 * through {@link sumTotals}, which is a stated rule rather than a spread.
 *
 * Ordering is `attributedMoney`'s rule, one level up (`fleet.ts:137-148`) and
 * for its reason: the rank arrives synchronously with `["agents"]` while a fold
 * arrives per server, one answer at a time, so sorting on the fold would
 * reorder the list under the reader's cursor as each server replied. The caller
 * hands the agents over already ranked and this preserves that order.
 */
export function mergeSlices(
  slices: readonly FloorSlice[],
  agents: readonly { slug: string; name: string }[],
): FloorModel {
  const byAgent = new Map<string, RowFold[]>();
  for (const slice of slices) {
    for (const [slug, fold] of slice.rows) {
      const held = byAgent.get(slug);
      if (held) held.push(fold);
      else byAgent.set(slug, [fold]);
    }
  }

  const rows: FloorRow[] = [];
  for (const agent of agents) {
    const folds = byAgent.get(agent.slug);
    if (!folds || folds.length === 0) continue;
    rows.push({
      slug: agent.slug,
      name: agent.name,
      reported: folds.some((fold) => fold.reported),
      ...mergeMoney(folds),
    });
  }

  const others = mergeKeyed<FloorOther>(
    slices.flatMap((slice) => slice.others),
    (a) => ({ key: a.key, kind: a.kind, label: a.label, scope: a.scope }),
  );
  const byPair = mergeKeyed<FloorBucket>(
    slices.flatMap((slice) => slice.byPair),
    (a) => ({ key: a.key, label: a.label }),
  );
  const byVenue = mergeKeyed<FloorBucket>(
    slices.flatMap((slice) => slice.byVenue),
    (a) => ({ key: a.key, label: a.label }),
  );

  const total = sumTotals(slices.map((slice) => slice.total));
  const parts =
    rows.reduce((sum, row) => sum + row.totals.net, 0) +
    others.reduce((sum, other) => sum + other.totals.net, 0);

  return {
    symbol: slices.find((slice) => slice.symbol)?.symbol ?? "$",
    rows,
    others,
    total,
    byPair: byPair.sort((a, b) => Math.abs(b.exposure) - Math.abs(a.exposure)),
    byVenue: byVenue.sort((a, b) => Math.abs(b.exposure) - Math.abs(a.exposure)),
    unaccounted: total.net - parts,
  };
}

/** Merge same-keyed money across servers, keeping the first entry's identity. */
function mergeKeyed<T extends FloorMoney & { key: string }>(
  items: readonly T[],
  identity: (item: T) => Omit<T, keyof FloorMoney>,
): T[] {
  const by = new Map<string, T[]>();
  for (const item of items) {
    const held = by.get(item.key);
    if (held) held.push(item);
    else by.set(item.key, [item]);
  }
  return [...by.values()].map(
    (group) => ({ ...identity(group[0]), ...mergeMoney(group) }) as T,
  );
}

// ── The two readings that survive a change of account size ──

/**
 * Fees as basis points of volume, or `null` when there is no volume to measure
 * against.
 *
 * **A floor, not a total**, and the caller has to say so on screen.
 * `leafFromController` hardcodes `fees: 0` — *"the controllers payload reports
 * no fee total of its own"* — so what this measures is the executors' fees over
 * everybody's volume. Printed without that caption it is a lie with a decimal
 * point on it.
 *
 * `null` rather than `0` for a zero denominator, for the reason
 * `attributedMoney` returns `null`: no statement is not zero, and the two look
 * identical on a screen that prints the number anyway.
 */
export function feeBps(totals: PerfTotals): number | null {
  if (!(totals.volume > 0)) return null;
  return (totals.fees / totals.volume) * 10_000;
}

/**
 * Volume as a multiple of declared capital — how many times the fleet turned
 * over what its controllers say they were given.
 *
 * `null` when nothing in scope declares any capital, which is a real state: a
 * fleet of standalone executors declares none at all, and `0×` would read as
 * "it never traded".
 */
export function turnover(totals: PerfTotals): number | null {
  if (!(totals.capital > 0)) return null;
  return totals.volume / totals.capital;
}

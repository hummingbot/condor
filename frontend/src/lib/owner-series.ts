// ── N owners, one timeline (FEAT-112) ──
//
// `aggregatePnlSeries` folds `ControllerPerformanceSnapshot[]` into one series.
// `PerfBrowser` already produces a *per-agent* series with it — one agent at a
// time, by narrowing `?scope=agent:{runKey}` down to `scopedKeys` and calling it
// once. What has never existed is N owners as N simultaneous series, because
// that function sums into fixed field names and has no per-key output.
//
// This module is that, and it is deliberately **not** a second fold. It calls
// `aggregatePnlSeries` once per owner — the identical call the browser makes
// for one — and once more over the union of their keys for the Total. Because
// that function forward-fills *per controller* and summation is linear, and
// because the `agent:` spines partition the root's spine, the Total line equals
// the sum of the owner lines at every instant, exactly. That is a property of
// the existing function rather than a promise made here; a test pins it, but
// nothing has to be kept in step by hand.
//
// The cost accepted is N+1 passes over the snapshot array. A bespoke fold that
// emitted `{ time, [key]: value }` in one pass would be faster and would be a
// second implementation of the rules in `aggregatePnlSeries` — the per-bucket
// volume clamp, the first-reading rule, the live "now" point — free to drift
// the moment one of them changes. Twelve agents over a memoised array is not a
// performance problem; a chart that disagrees with `/bots` is a correctness one.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { aggregatePnlSeries, type PnlChartPoint } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";

/** One owner's line: who it is, and the series it draws. */
export interface OwnerSeries {
  /** Stable across polls — an agent slug, or a named bucket's value. */
  key: string;
  label: string;
  points: PnlChartPoint[];
}

/** An owner, as the chart needs to be told about one. */
export interface SeriesOwner {
  key: string;
  label: string;
  /** Its controller keys (`bot:controller_id`), off the fold's own spine. */
  keys: readonly string[];
}

/**
 * One `aggregatePnlSeries` call per owner, plus one for the whole set.
 *
 * The Total is folded over the **union of the owners' keys**, not over every
 * controller on the server, and that is what makes the invariant exact rather
 * than approximate. The caller's job is therefore to hand over *every* part of
 * the partition — the agents and the named non-agent buckets alike — so that
 * the union is the fleet and the Total is the fleet's line. Handing over only
 * some of them draws an honest total of those, which is the other useful thing
 * this signature can express.
 */
export function ownerSeries(
  snapshots: ControllerPerformanceSnapshot[],
  owners: readonly SeriesOwner[],
  controllers: ControllerInfo[],
  convert?: ConvertFn,
): { total: PnlChartPoint[]; owners: OwnerSeries[] } {
  // `aggregatePnlSeries` stamps its live point with `Date.now()` at call time,
  // so N+1 calls can land on N+1 consecutive milliseconds. Snapped to one
  // instant below, because the whole claim of this page is that the owner lines
  // add up to the total at *every* instant — and a one-millisecond stagger puts
  // the total's live point on a timeline instant where every owner still reads
  // its last stored snapshot, which the merge would then draw as a step.
  const before = Date.now();

  const all = new Set<string>();
  for (const owner of owners) for (const key of owner.keys) all.add(key);

  const total = aggregatePnlSeries(snapshots, all, controllers, convert);
  const lines = owners.map((owner) => ({
    key: owner.key,
    label: owner.label,
    points: aggregatePnlSeries(snapshots, new Set(owner.keys), controllers, convert),
  }));

  const series = [total, ...lines.map((line) => line.points)];
  let liveAt = 0;
  for (const points of series) {
    const last = points[points.length - 1];
    if (last && last.time >= before) liveAt = Math.max(liveAt, last.time);
  }
  if (liveAt > 0) {
    for (const points of series) {
      const last = points[points.length - 1];
      // A stored snapshot is always in the past, so `>= before` identifies the
      // appended live point and nothing else.
      if (last && last.time >= before) last.time = liveAt;
    }
  }

  return { total, owners: lines };
}

/** The recharts `dataKey` an owner's line is drawn under. */
export function ownerDataKey(key: string): string {
  // Namespaced so an owner whose slug happens to be `total` or `volume` cannot
  // overwrite the fleet fields sharing the row object with it.
  return `owner:${key}`;
}

/** One merged row: every fleet field, plus one value per owner. */
export interface FloorChartRow extends PnlChartPoint {
  [key: string]: number;
}

/**
 * N series onto one timeline, as the wide rows recharts draws from.
 *
 * Each owner is **forward-filled** onto the union timeline, which is correct
 * for the same reason `aggregatePnlSeries`' own fill is: a controller's value
 * is constant between its own snapshots, so an owner's value at the largest
 * owner-instant `≤ t` is exactly `Σ` of its controllers at `t`.
 *
 * Only the **stock** fields carry across — `total`, `realized`, `unrealized`,
 * `volume`, `position`. `volumeDelta` is a per-bucket **flow**: forward-filling
 * it would charge the same trading to every later bucket, so it is summed at
 * the instants that actually carry it and is zero everywhere else.
 *
 * `total` takes an array of series rather than one because a caller may hold
 * several folds that belong on one timeline — one per server, each folded with
 * its own currency converter, which is the shape the retired floor page had.
 * Owners may likewise repeat a key across those folds; repeats are summed,
 * which is what makes an agent trading on two servers one line. `/bots` is one
 * server and hands over one.
 */
export function mergeOwnerRows(
  total: readonly PnlChartPoint[][],
  owners: readonly OwnerSeries[],
): { rows: FloorChartRow[]; keys: string[] } {
  const times = new Set<number>();
  for (const series of total) for (const point of series) times.add(point.time);
  for (const owner of owners) for (const point of owner.points) times.add(point.time);
  const timeline = [...times].sort((a, b) => a - b);
  if (timeline.length === 0) return { rows: [], keys: [] };

  // Insertion order, so the legend and the drawn lines keep the order the
  // caller ranked its owners in rather than the order a Map happened to hash.
  const keys: string[] = [];
  const byKey = new Map<string, PnlChartPoint[][]>();
  for (const owner of owners) {
    const held = byKey.get(owner.key);
    if (held) held.push(owner.points);
    else {
      keys.push(owner.key);
      byKey.set(owner.key, [owner.points]);
    }
  }

  const cursors = new Map<readonly PnlChartPoint[], number>();
  /** The stock value of one series at `t`, forward-filled; `null` before it starts. */
  const at = (series: readonly PnlChartPoint[], t: number): PnlChartPoint | null => {
    let i = cursors.get(series) ?? 0;
    while (i < series.length - 1 && series[i + 1].time <= t) i++;
    cursors.set(series, i);
    const point = series[i];
    return point && point.time <= t ? point : null;
  };

  const rows: FloorChartRow[] = [];
  for (const t of timeline) {
    const row = {
      time: t,
      realized: 0,
      unrealized: 0,
      total: 0,
      volume: 0,
      volumeDelta: 0,
      position: 0,
    } as FloorChartRow;
    for (const series of total) {
      const point = at(series, t);
      if (!point) continue;
      row.realized += point.realized;
      row.unrealized += point.unrealized;
      row.total += point.total;
      row.volume += point.volume;
      row.position += point.position;
      // The flow, charged only to the bucket that recorded it.
      if (point.time === t) row.volumeDelta += point.volumeDelta;
    }
    for (const key of keys) {
      let value = 0;
      let seen = false;
      for (const series of byKey.get(key)!) {
        const point = at(series, t);
        if (!point) continue;
        seen = true;
        value += point.total;
      }
      // An owner that has not started yet contributes no point at all rather
      // than a zero: recharts draws a gap, which is the truth, where a zero
      // would draw a flat line along the axis for trading that had not begun.
      if (seen) row[ownerDataKey(key)] = value;
    }
    rows.push(row);
  }
  return { rows, keys };
}

/** Absolute quote PnL, or a percentage of declared capital. */
export type Basis = "abs" | "rel";
/** Measured from the first record, or from the start of the window on screen. */
export type Baseline = "inception" | "window";

/** `?basis=` — falls back to the default rather than throwing (`parsePopulation`'s rule). */
export function parseBasis(raw: string | null): Basis {
  return raw === "rel" ? "rel" : "abs";
}

/** `?from=` — same rule: a stale parameter lands on the page that was asked for. */
export function parseBaseline(raw: string | null): Baseline {
  return raw === "window" ? "window" : "inception";
}

/** An owner the chart cannot plot, and the reason, for the legend to state. */
export interface Unplottable {
  key: string;
  reason: "no declared capital";
}

/**
 * The four toggle states, applied to rows already merged and already sliced to
 * the window on screen.
 *
 * |            | Inception            | Window                        |
 * |------------|----------------------|-------------------------------|
 * | Absolute   | `v(t)`               | `v(t) − v(t₀)`                |
 * | Relative   | `v(t) / capital × 100` | `(v(t) − v(t₀)) / capital × 100` |
 *
 * Separate from {@link mergeOwnerRows} because the window is the reader's, not
 * the data's: the rows are merged once over everything loaded and rebased on
 * whatever slice is drawn, so switching baseline costs no refold.
 *
 * `capital` is the owner's `PerfTotals.capital` — the `total_amount_quote` its
 * controllers declare. An owner whose scope declares none has capital `0`, and
 * dividing by it prints an infinity or a zero that both read as a fact. Those
 * owners come back in `unplottable` instead, to be **listed under the legend
 * and not drawn** — `attributedMoney`'s rule again: no statement is not `0`.
 */
export function rebaseRows(
  rows: readonly FloorChartRow[],
  keys: readonly string[],
  {
    basis,
    from,
    capital,
  }: { basis: Basis; from: Baseline; capital: Readonly<Record<string, number>> },
): { rows: FloorChartRow[]; unplottable: Unplottable[] } {
  const fields = ["total", ...keys.map(ownerDataKey)];
  const unplottable: Unplottable[] =
    basis === "rel"
      ? [...keys, "total"]
          .filter((key) => !((capital[key] ?? 0) > 0))
          .map((key) => ({ key, reason: "no declared capital" as const }))
      : [];
  const dropped = new Set(unplottable.map((u) => ownerDataKey(u.key)));
  if (unplottable.some((u) => u.key === "total")) dropped.add("total");

  if (basis === "abs" && from === "inception") {
    return { rows: rows as FloorChartRow[], unplottable };
  }

  // The baseline is each field's **first drawn value** in the window, not the
  // first row's: an owner whose line starts halfway through the window has no
  // value at `t₀` to be a difference from, and rebasing it against zero would
  // draw its whole cumulative PnL as if it had been earned inside the window.
  const base = new Map<string, number>();
  if (from === "window") {
    for (const row of rows) {
      for (const field of fields) {
        if (!base.has(field) && typeof row[field] === "number") base.set(field, row[field]);
      }
    }
  }

  const scale = (field: string): number => {
    if (basis === "abs") return 1;
    const key = field === "total" ? "total" : field.slice("owner:".length);
    return 100 / (capital[key] ?? 0);
  };

  return {
    rows: rows.map((row) => {
      const next = { ...row } as FloorChartRow;
      for (const field of fields) {
        if (dropped.has(field)) {
          delete next[field];
          continue;
        }
        if (typeof row[field] !== "number") continue;
        next[field] = (row[field] - (base.get(field) ?? 0)) * scale(field);
      }
      return next;
    }),
    unplottable,
  };
}

/** The eight categorical tokens, cycled — see the note beside them in index.css. */
export function seriesColor(index: number): string {
  return `var(--chart-series-${(Math.max(0, index) % 8) + 1})`;
}

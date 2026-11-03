// ── Shared helpers for PNL evolution charts ──

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { toMs } from "@/lib/formatters";
import type { ConvertFn } from "@/lib/rates";
import { PNL_HIDDEN_SERIES_KEY } from "@/lib/sessionState";

/**
 * Fixed series colors shared by strokes, axis ticks, header stats and tooltips
 * across PerfBrowser and ControllerPnlChart. Realized and total are
 * theme-driven (getThemeColors / pnlColor), so only the three fixed series live here.
 */
export const PNL_SERIES_COLORS = {
  unrealized: "#f59e0b",
  volume: "#3b82f6",
  position: "#a78bfa",
} as const;

/**
 * The name of each pane, in the one place both the panes and the things that
 * point at them can read it (READ-244, READ-247, READ-248).
 *
 * Three separate surfaces name these two panes: the caption drawn above each
 * one, the two groups the header legend sorts the five series into, and the two
 * sections of the hover card. They are the same two words or the grouping stops
 * being a grouping — a card headed "Trading" over a pane captioned "Activity"
 * is a card about some other pane.
 */
export const PANE_LABELS = { pnl: "PnL", activity: "Activity" } as const;

/**
 * The one name each drawn series goes by, keyed by the `dataKey` it is drawn
 * from (READ-244).
 *
 * Five series used to be spelled three different ways at once: the recharts
 * `<Legend>` on the PNL pane capitalised raw dataKeys and covered only the
 * three series in that pane, the header strip abbreviated to `R:` / `U:` /
 * `Vol:` / `Pos:`, and the tooltips used full words of their own. Three
 * vocabularies meant nothing on screen could be matched to anything else on
 * screen by reading it — and the two series in the lower pane were never named
 * at all, decodable only by matching a stroke colour to a coloured axis tick.
 *
 * The legend in the chart header and both tooltips now read from here, so a
 * series is renamed in one place or not at all.
 *
 * `volumeDelta` is deliberately just "Volume": what that series needs beside it
 * is not a longer noun but the length of one bar, which is data rather than a
 * name — see `formatBucketLabel`. And `position` is "Net position", not
 * "Position", because `positionQuoteValue` returns a *signed* notional that
 * nets longs against shorts; the word carries the same meaning the signed area
 * and its zero baseline draw (READ-246).
 */
export const PNL_SERIES_LABELS = {
  total: "Total",
  realized: "Realized",
  unrealized: "Unrealized",
  volumeDelta: "Volume",
  position: "Net position",
} as const;

/** The one key each drawn series is known by, everywhere (READ-244). */
export type PnlSeriesKey = keyof typeof PNL_SERIES_LABELS;

/**
 * Which pane draws each series.
 *
 * The legend groups its entries by this, the chart decides from it whether a
 * pane still has anything left to draw, and the "you cannot hide the last one"
 * guard counts within it. Written out rather than derived from the JSX so the
 * three of them cannot disagree about where a series lives — the same reason
 * PNL_SERIES_LABELS is one map rather than three spellings.
 */
export const PNL_SERIES_PANE: Record<PnlSeriesKey, keyof typeof PANE_LABELS> = {
  total: "pnl",
  realized: "pnl",
  unrealized: "pnl",
  volumeDelta: "activity",
  position: "activity",
};

/** The series of one pane, in the order the legend lists them. */
export function paneSeries(pane: keyof typeof PANE_LABELS): PnlSeriesKey[] {
  return (Object.keys(PNL_SERIES_PANE) as PnlSeriesKey[]).filter((k) => PNL_SERIES_PANE[k] === pane);
}

// ── Which series the charts draw (FEAT-085) ──
//
// A module-level store rather than component state, for the reason the
// preference itself is device-wide: every PNL chart in the app draws the same
// set, and two of them mounted at once — the browser's aggregate chart and a
// controller's — disagreeing about it would be a legend that lies about the
// chart next to it.
//
// `localStorage` is the truth and is re-read on every snapshot rather than
// cached behind a flag. It is one short string, `useSyncExternalStore` calls
// this a handful of times per render, and reading through means a second tab
// (or a test that seeds the key before the first render) is picked up without a
// separate invalidation path. What *is* cached is the parsed Set, keyed on the
// raw string it came from: the hook compares snapshots with `Object.is`, so
// returning a fresh Set each call would re-render forever.

const NO_HIDDEN_SERIES: ReadonlySet<PnlSeriesKey> = new Set();

/** The raw string `cachedHidden` was parsed from; `undefined` = never read. */
let cachedRawHidden: string | null | undefined;
let cachedHidden: ReadonlySet<PnlSeriesKey> = NO_HIDDEN_SERIES;
const hiddenListeners = new Set<() => void>();

function parseHiddenSeries(raw: string | null): ReadonlySet<PnlSeriesKey> {
  if (!raw) return NO_HIDDEN_SERIES;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return NO_HIDDEN_SERIES;
    // Filtered against the labels rather than trusted: the key outlives the
    // series it names, and a renamed one left in storage would otherwise hide
    // nothing forever while still counting towards the last-series guard.
    return new Set(parsed.filter((k): k is PnlSeriesKey => typeof k === "string" && k in PNL_SERIES_LABELS));
  } catch {
    return NO_HIDDEN_SERIES;
  }
}

/** Subscribe a chart to the shared choice. Pairs with `hiddenSeriesSnapshot`. */
export function subscribeToHiddenSeries(onChange: () => void) {
  hiddenListeners.add(onChange);
  return () => {
    hiddenListeners.delete(onChange);
  };
}

/** The series currently switched off, as a stable Set until the choice changes. */
export function hiddenSeriesSnapshot(): ReadonlySet<PnlSeriesKey> {
  let raw: string | null;
  try {
    raw = localStorage.getItem(PNL_HIDDEN_SERIES_KEY);
  } catch {
    // Storage disabled: the last thing written in this tab is all there is.
    return cachedHidden;
  }
  if (raw !== cachedRawHidden) {
    cachedRawHidden = raw;
    cachedHidden = parseHiddenSeries(raw);
  }
  return cachedHidden;
}

/**
 * Switch one series off, or back on, for every chart on this device.
 *
 * The cache is written before the store is, so a browser that refuses the write
 * still honours the click for as long as the tab lives — a toggle that silently
 * does nothing is worse than one that merely forgets overnight.
 */
export function setSeriesHidden(series: PnlSeriesKey, hide: boolean) {
  const next = new Set(hiddenSeriesSnapshot());
  if (hide) next.add(series);
  else next.delete(series);
  // Sorted so the same set is always the same string, and the snapshot's
  // identity therefore survives a toggle that lands back where it started.
  const raw = JSON.stringify([...next].sort());
  cachedRawHidden = raw;
  cachedHidden = next;
  try {
    localStorage.setItem(PNL_HIDDEN_SERIES_KEY, raw);
  } catch {
    // See above: the in-memory cache above already took the change.
  }
  for (const listener of [...hiddenListeners]) listener();
}

/**
 * Width in px reserved by every YAxis gutter in a PNL evolution chart.
 *
 * The PNL pane and the volume/position pane below it are two separate charts
 * tied together only by `syncId`: recharts syncs the cursor and the tooltip
 * index, never the geometry. Each pane computes its own plot area as
 * (container width - left gutter - right gutter), so the two plot areas land on
 * the same x pixels — and a given instant sits under the same column in both —
 * only for as long as their gutters add up to the same total. That is why both
 * panes in PnlEvolutionChart render AXIS_WIDTH on the left *and* AXIS_WIDTH on
 * the right unconditionally: the PNL pane's right-hand axis (`yAxisId="spacer"`)
 * is empty and the bottom pane's is the position axis, but they are always both
 * there, so the geometry cannot depend on whether there is a position to label
 * and cannot shift under the user when one opens or closes. Only the ticks come
 * and go with the data.
 *
 * So this one number is a contract, not a style choice. Change it here and both
 * panes move together; hard-code a different value at one axis — to fit a longer
 * tick label, say — and the panes silently drift apart, with the grid lines and
 * the synced cursor of the top pane pointing at a different instant than the
 * bottom one. Nothing throws when that happens. Every YAxis in both panes must
 * read its width from here, and an axis added to one pane needs its mirror in
 * the other.
 */
export const AXIS_WIDTH = 52;

/**
 * The other two numbers that decide where a pane's plot area starts and stops:
 * the horizontal padding each pane wrapper puts around its chart, and the
 * right-hand margin every ComposedChart is given (the left margin is 0).
 *
 * They live here beside AXIS_WIDTH because the rule that separates the two
 * panes (READ-247) is inset to the plot area, and the only way to know where
 * the plot area is, is to add these to the gutter. Change one of them in the
 * JSX without changing it here and the rule stops tracing the grid it is drawn
 * to trace — the same silent drift AXIS_WIDTH exists to prevent, so both panes
 * read their padding and margin from here too.
 */
export const PANE_PAD_X = 4;
export const PANE_MARGIN_RIGHT = 12;

/** Where a pane's plot area begins and ends, measured from the card's edges. */
export const PLOT_INSET_LEFT = PANE_PAD_X + AXIS_WIDTH;
export const PLOT_INSET_RIGHT = PANE_PAD_X + PANE_MARGIN_RIGHT + AXIS_WIDTH;

/**
 * How much headroom the position axis keeps beyond the series, as a fraction of
 * its own span. Small enough not to flatten the shape, big enough that a series
 * that never changes sign still shows its zero line as a *line* rather than as
 * the pane's bottom (or top) edge.
 */
export const POSITION_AXIS_PAD = 0.08;

/** A single point on a PNL evolution chart (per-controller or aggregated). */
export interface PnlChartPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  /** Cumulative volume traded, summed across the enabled controllers. */
  volume: number;
  /**
   * Volume traded *in this sampling bucket alone* — the flow behind `volume`'s
   * stock (READ-245).
   *
   * `volume_traded` is a running counter, so a series drawn from it can only
   * ever slope up-right: it says how much has been traded since the bots were
   * deployed and nothing at all about *when*. The delta says when. It is what
   * the activity pane draws as bars, and it is computed here rather than by
   * diffing `volume` afterwards for two reasons the summed series cannot
   * recover from:
   *
   *  - **A controller's first appearance.** The fold forward-fills, so a
   *    controller that joins the fleet mid-window contributes nothing before
   *    its first snapshot and its whole cumulative counter after it. On the
   *    summed series that arrives as one enormous step, which a post-hoc diff
   *    reads as a bucket in which the fleet traded everything it has ever
   *    traded. Per controller it is recognisable as what it is: a first
   *    reading, with no predecessor to be a difference from, and so worth no
   *    bar at all.
   *  - **A restart.** A controller that restarts resets its counter, which
   *    shows up as a fall. Clamped per controller, that one controller
   *    contributes 0 for that bucket; clamped on the summed series, its fall
   *    would cancel every *other* controller's real trading in the same bucket
   *    and the bar would vanish.
   *
   * Both are the same principle: the diff belongs where the counter lives.
   *
   * What the bars therefore total is the volume traded **over the window on
   * screen**, not since deploy — the opening reading of each controller is the
   * baseline the rest are measured from, not a bar. The lifetime figure is the
   * header's Vol stat, and the two are different quantities rather than a
   * disagreement: this series is the flow, that one is the stock. Charging the
   * opening reading to the first bucket to make the two tally is what the
   * first draft of this did, and on any window shorter than the fleet's life
   * it put millions into one bar and scaled the axis to it, flattening every
   * real bucket to a pixel.
   */
  volumeDelta: number;
  position: number;
}

/** Compute net position value in quote from positions_summary */
export function positionQuoteValue(positions: Record<string, unknown>[]): number {
  let value = 0;
  for (const pos of positions) {
    const amt = Number(pos.amount || pos.net_amount_base || 0);
    const price = Number(pos.breakeven_price || pos.entry_price || pos.current_price || 0);
    const side = String(pos.side || pos.position_side || "");
    const isSell = side.toLowerCase().includes("sell") || side.toLowerCase().includes("short");
    const notional = amt * price;
    value += isSell ? -notional : notional;
  }
  return value;
}

/**
 * The Y domain for the position axis — always straddling zero (READ-246).
 *
 * `positionQuoteValue` above returns a *signed* notional: a short subtracts, so
 * the sign of `position` is the single most important thing about it, and the
 * series is drawn as an area filled from zero to say so. That only reads
 * correctly if zero is actually inside the axis, which recharts' default
 * `[0, "auto"]` does not guarantee: a user-provided bound is only ever widened
 * to fit the data (`allowDataOverflow` is false), so a book that has been net
 * short all session gets a domain of, say, [-800, -120] and an "area from zero"
 * whose zero is off the top of the pane — filled edge to edge, sign invisible.
 *
 * So both ends are clamped through zero and then padded. The padding is what
 * makes the never-flipped cases legible: with a bare [0, max] an all-long book
 * would have its zero line sitting exactly on the pane's bottom edge, which
 * reads as a border, not as a baseline the fill grows out of.
 */
export function positionAxisDomain(data: PnlChartPoint[]): [number, number] {
  const [min, max] = positionAreaExtent(data);
  // An all-zero (or empty) series has no span of its own to take a fraction of;
  // any symmetric domain will do, since nothing is drawn on it.
  const pad = (max - min || 1) * POSITION_AXIS_PAD;
  return [min - pad, max + pad];
}

/**
 * How far the drawn area actually reaches, top and bottom: the series' own
 * extremes, each clamped through zero because the area is filled *from* zero
 * and so always touches it.
 *
 * This is deliberately the unpadded extent, and it is not the axis domain. See
 * `zeroGradientOffset`.
 */
export function positionAreaExtent(data: PnlChartPoint[]): [number, number] {
  let min = 0;
  let max = 0;
  for (const point of data) {
    if (point.position < min) min = point.position;
    if (point.position > max) max = point.position;
  }
  return [min, max];
}

/**
 * Where zero falls inside `extent`, as a 0..1 offset measured from the top.
 *
 * The signed area is filled from a vertical gradient with two stops at this
 * exact offset — the long colour above it, the short colour below — which is
 * how one `<Area>` shows two sides without splitting the series in two.
 *
 * The subtlety, and the reason this takes the *area's extent* rather than the
 * axis domain it would be natural to reach for: an SVG gradient's default
 * `gradientUnits` is `objectBoundingBox`, so offset 0 and offset 1 are the top
 * and bottom of the **filled path**, not of the plot area. The filled path runs
 * from the series' highest point to its lowest, both clamped through zero — the
 * axis's padding is not part of it. Feeding the padded domain in here puts the
 * colour change a few percent off the baseline, which on a book that never
 * changes sign shows up as a band of the *wrong* colour hugging the zero line.
 */
export function zeroGradientOffset([min, max]: [number, number]): number {
  if (!(max > min)) return 0.5;
  return Math.min(1, Math.max(0, max / (max - min)));
}

/**
 * Fold per-controller performance snapshots into one timeline of chart points.
 *
 * The whole snapshot → chart pipeline lives here, out of the components that
 * draw it (ARCH-243), so it can be tested on its own: the components pass data
 * in and render what comes back.
 *
 * The shape of the fold:
 *  - Snapshots are grouped by `controllerKey` — the bot joined to the
 *    controller id, because the id alone is a *config* id two bots can share
 *    (CORR-241) — and `enabledIds` holds those same composite keys; anything
 *    not in it is dropped entirely, so a controller toggled off contributes to
 *    no point at all.
 *  - Every distinct snapshot timestamp across the enabled controllers becomes
 *    one point on a single, ascending, de-duplicated timeline — input order
 *    does not matter.
 *  - Each controller is then **forward-filled** onto that timeline: at time `t`
 *    it contributes its latest snapshot at or before `t`, so a controller with
 *    a sparse series keeps counting after its last snapshot, and one that only
 *    starts later contributes nothing before its first.
 *  - Values are converted into the display currency through `convertFn`, using
 *    the quote of the snapshot's own `trading_pair` when it has one and the
 *    live controller's pair otherwise (defaulting to USDT).
 *  - Finally a live "now" point is appended from `controllers`, so the chart
 *    ends at real-time values rather than at the last stored snapshot.
 *  - Alongside the cumulative `volume`, each point carries `volumeDelta`: how
 *    much was traded since that controller's *previous* value, summed and
 *    clamped per controller (READ-245, see PnlChartPoint). Because the
 *    forward-fill re-uses a controller's last value verbatim, a bucket in
 *    which it produced no new snapshot diffs to exactly zero — no bar — which
 *    is the whole point of drawing the flow.
 */
/** A snapshot paired with its timestamp already resolved to epoch ms (PERF-282). */
type DatedSnapshot = { ms: number; snap: ControllerPerformanceSnapshot };

export function aggregatePnlSeries(
  snapshots: ControllerPerformanceSnapshot[],
  enabledIds: Set<string>,
  controllers: ControllerInfo[],
  convertFn?: ConvertFn,
): PnlChartPoint[] {
  if (!snapshots || snapshots.length === 0) return [];

  // Build a lookup from controller key -> trading_pair using live controller data
  const pairByCtrl: Record<string, string> = {};
  for (const ctrl of controllers) {
    const cid = controllerKey(ctrl);
    if (cid && ctrl.trading_pair) pairByCtrl[cid] = ctrl.trading_pair;
  }

  const cv = (val: number, pair: string) => {
    if (!convertFn) return val;
    const quote = pair?.split("-")[1] || "USDT";
    return convertFn(val, quote).value;
  };

  // Each row carries the epoch-ms of its own timestamp, parsed here and only
  // here (PERF-282). `timestamp` is an ISO string, so `toMs` is a `Date.parse`;
  // read straight off the snapshot it would be paid again on every comparison
  // of the sort below (~S·log S), again per row for the timeline, and again
  // once per (instant × controller) in the forward-fill — six figures of
  // parsing per fold on a large fleet, redone every time the `bots` socket
  // mints a new `controllers` array.
  const byCtrl: Record<string, DatedSnapshot[]> = {};
  for (const snap of snapshots) {
    const key = controllerKey(snap);
    if (!key || !enabledIds.has(key)) continue;
    (byCtrl[key] ??= []).push({ ms: toMs(snap.timestamp), snap });
  }

  // Sorting on the decorated `ms` keeps the comparator's semantics exactly:
  // equal instants stay in input order, because Array#sort is stable.
  for (const snaps of Object.values(byCtrl)) {
    snaps.sort((a, b) => a.ms - b.ms);
  }

  const timeSet = new Set<number>();
  for (const snaps of Object.values(byCtrl))
    for (const s of snaps) timeSet.add(s.ms);
  const times = Array.from(timeSet).sort((a, b) => a - b);
  if (times.length === 0) return [];

  const cids = Object.keys(byCtrl);
  const cursors: Record<string, number> = {};
  for (const c of cids) cursors[c] = 0;

  // The cumulative volume each controller last contributed, in display
  // currency. This is what the per-bucket delta is measured against, and it is
  // deliberately the last *value* rather than the last snapshot index: a
  // forward-filled bucket re-reads the same snapshot, so it diffs to zero on
  // its own without a separate "did this controller move" flag.
  const prevVolume: Record<string, number> = {};

  const points: PnlChartPoint[] = [];
  for (const t of times) {
    let realized = 0, unrealized = 0, volume = 0, volumeDelta = 0, position = 0;
    for (const cid of cids) {
      const snaps = byCtrl[cid];
      while (cursors[cid] < snaps.length - 1 && snaps[cursors[cid] + 1].ms <= t)
        cursors[cid]++;
      if (snaps[cursors[cid]].ms <= t) {
        const s = snaps[cursors[cid]].snap;
        const pair = s.trading_pair || pairByCtrl[cid] || "";
        realized += cv(s.realized_pnl_quote, pair);
        unrealized += cv(s.unrealized_pnl_quote, pair);
        const vol = cv(s.volume_traded, pair);
        volume += vol;
        // A controller's *first* reading is worth no bar at all. It has no
        // predecessor to be a difference from, and its absolute value is a
        // stock — everything the controller has ever traded — which is the one
        // quantity this series exists to stop drawing. Charging it to the
        // opening bucket puts the whole lifetime counter into one bar and
        // scales the axis to it, flattening every real bucket to nothing.
        const prev = prevVolume[cid];
        if (prev !== undefined) volumeDelta += Math.max(0, vol - prev);
        prevVolume[cid] = vol;
        if (Array.isArray(s.positions_summary)) {
          position += cv(positionQuoteValue(s.positions_summary as Record<string, unknown>[]), pair);
        }
      }
    }
    points.push({ time: t, realized, unrealized, total: realized + unrealized, volume, volumeDelta, position });
  }

  // Append a live "now" point from controllers so the graph ends at real-time values
  const now = Date.now();
  let liveRealized = 0, liveUnrealized = 0, liveVolume = 0, liveVolumeDelta = 0, livePosition = 0;
  let hasLive = false;
  for (const ctrl of controllers) {
    const cid = controllerKey(ctrl);
    if (!cid || !enabledIds.has(cid)) continue;
    hasLive = true;
    const pair = ctrl.trading_pair || "";
    liveRealized += cv(ctrl.realized_pnl_quote, pair);
    liveUnrealized += cv(ctrl.unrealized_pnl_quote, pair);
    const vol = cv(ctrl.volume_traded, pair);
    liveVolume += vol;
    // The live point closes an *in-progress* bucket: whatever the counter has
    // moved since this controller's last stored snapshot. Its bar is therefore
    // honestly short until the bucket fills — and a controller with no stored
    // snapshot at all still gets no bar, for the same reason as above.
    const prev = prevVolume[cid];
    if (prev !== undefined) liveVolumeDelta += Math.max(0, vol - prev);
    if (Array.isArray(ctrl.positions_summary)) {
      livePosition += cv(positionQuoteValue(ctrl.positions_summary as Record<string, unknown>[]), pair);
    }
  }
  if (hasLive) {
    points.push({
      time: now,
      realized: liveRealized,
      unrealized: liveUnrealized,
      total: liveRealized + liveUnrealized,
      volume: liveVolume,
      volumeDelta: liveVolumeDelta,
      position: livePosition,
    });
  }

  return points;
}

/** The part of a finished record this series is built from. */
export interface ClosedOutcome {
  /** Epoch ms; a record with no end contributes nothing. */
  endedAt: number | null;
  /** Final PnL and volume traded, in this record's own quote. */
  net: number;
  volume: number;
  pair: string;
}

/**
 * A cumulative series drawn from a set of closed executors (FEAT-086).
 *
 * The controller chart above folds *sampled history*: rows written every five
 * minutes by an upstream sampler. Executors have no such history — upstream
 * stores one mutable row per executor, updated in place — so a terminated scope
 * has no series to fold and would otherwise be a strip with an empty pane under
 * it.
 *
 * It does have something better than an approximation, though: a closed
 * executor is a *known outcome at a known instant*. Its final PnL and its close
 * time are both recorded, so a running cumulative sum over those instants is a
 * real series — not a resampling, not an interpolation, and not a guess. It is
 * a different kind of series from the one above it, and the caller must say so
 * on the card (see the "closed outcomes" notice in PerfBrowser); this function
 * returns `PnlChartPoint[]` so the chart itself needs no change at all.
 *
 * What follows from "these are outcomes, not samples":
 *  - `realized` and `total` are the same running sum, and `unrealized` is 0.
 *    Nothing here is open; there is no mark to be marked to.
 *  - `position` is 0 for every point, which the chart reads as "no position
 *    series" and drops from the lower pane, leaving the volume bars alone.
 *    A closed set holds nothing.
 *  - Closes are bucketed rather than drawn one point each: a terminated
 *    population is tens of thousands of executors, and the chart is a thousand
 *    pixels wide. The bucket comes off the same PERF-238 ladder the sampled
 *    history uses, chosen from the span so the point count fits `budget` — so
 *    the two kinds of series are drawn at comparable resolutions.
 *  - A point sits at its bucket's **end**, which is when its closes had all
 *    happened. The last one is pulled back to the final close rather than
 *    running on to the end of a bucket that has not finished, and an opening
 *    zero is placed at the first bucket's start so the first step is a step up
 *    from nothing rather than a line beginning mid-air.
 *
 * Takes the shape it needs rather than a `PerfLeaf`, so the chart layer stays
 * independent of the tree that happens to feed it today.
 *
 * `volumeDelta` is the volume closed *in that bucket* — the flow — while
 * `volume` is the running total, the same division of labour as the sampled
 * series (READ-245). Here the flow needs no clamping: an executor's volume is
 * final and is counted once, at its close, so there is no counter to reset.
 */
export function executorSeries(
  leaves: readonly ClosedOutcome[],
  cv: (value: number, pair: string) => number,
  budget: number = HISTORY_POINT_BUDGET,
): PnlChartPoint[] {
  const closed = leaves
    .filter((leaf) => leaf.endedAt !== null)
    .sort((a, b) => a.endedAt! - b.endedAt!);
  if (closed.length === 0) return [];

  const first = closed[0].endedAt!;
  const last = closed[closed.length - 1].endedAt!;
  const span = last - first;
  // The ladder is the set of intervals the *history route* accepts, and it
  // stops at a day. This series is bucketed on the client and has no such
  // ceiling, so a span long enough to overrun the budget even at the coarsest
  // rung is widened to a whole number of them — which keeps buckets aligned to
  // a readable unit instead of landing on an arbitrary fraction of a day.
  const rung = samplingIntervalMs(pickSamplingInterval(span, budget));
  const bucketMs =
    span / rung <= budget ? rung : Math.ceil(span / budget / rung) * rung;

  const buckets = new Map<number, { net: number; volume: number }>();
  for (const leaf of closed) {
    const start = Math.floor(leaf.endedAt! / bucketMs) * bucketMs;
    const acc = buckets.get(start) ?? { net: 0, volume: 0 };
    acc.net += cv(leaf.net, leaf.pair);
    acc.volume += cv(leaf.volume, leaf.pair);
    buckets.set(start, acc);
  }

  const starts = [...buckets.keys()].sort((a, b) => a - b);
  // Where the series begins: flat at zero, just before the first outcome.
  const points: PnlChartPoint[] = [
    { time: starts[0], realized: 0, unrealized: 0, total: 0, volume: 0, volumeDelta: 0, position: 0 },
  ];
  let net = 0;
  let volume = 0;
  for (const start of starts) {
    const acc = buckets.get(start)!;
    net += acc.net;
    volume += acc.volume;
    points.push({
      time: Math.min(start + bucketMs, last),
      realized: net,
      unrealized: 0,
      total: net,
      volume,
      volumeDelta: acc.volume,
      position: 0,
    });
  }
  return points;
}

/**
 * A cached run's points, back in the shape the fleet's chart already folds
 * (FEAT-089).
 *
 * The wire sends six bare floats per point rather than an object, because there
 * are up to a thousand of them per controller and the field names would be most
 * of the bytes. Expanding here rather than teaching `aggregatePnlSeries` a
 * second input shape is the whole reason a terminated scope draws *the same
 * chart* the live one draws: one fold, one forward-fill, one set of series, so
 * a run cannot be measured one way while it is trading and another way once it
 * is over.
 *
 * `positions_summary` is empty on purpose. These points are a finished run's
 * history, and the position series answers "what is open right now" — which for
 * a run that has stopped is nothing. Synthesising one from the PnL would draw a
 * position nobody holds.
 */
export function snapshotsFromRunHistory(
  history: {
    controllers: Record<string, number[][]>;
    identities: Record<string, { connector: string; trading_pair: string }>;
  },
  botName: string,
): ControllerPerformanceSnapshot[] {
  const out: ControllerPerformanceSnapshot[] = [];
  for (const [controllerId, points] of Object.entries(history.controllers ?? {})) {
    const identity = history.identities?.[controllerId];
    for (const [t, realized, unrealized, net, volume, pct] of points) {
      out.push({
        // Not free, and deliberately so. `ControllerPerformanceSnapshot`
        // mirrors the Pydantic model the live socket sends, where `timestamp`
        // is a string (api.ts), and minting a *genuine* snapshot is the whole
        // point of this function — it is what lets a terminated run reuse the
        // live fold unchanged. So the epoch is formatted to ISO here and parsed
        // back by `toMs` in `aggregatePnlSeries` (PERF-282): a lossless round
        // trip costing ~0.5µs per point, paid once per fold over at most a
        // thousand points per controller. Widening the wire type to dodge it
        // would make every consumer accept a shape the server never sends.
        timestamp: new Date(t).toISOString(),
        bot_name: botName,
        controller_id: controllerId,
        controller_name: "",
        connector: identity?.connector ?? "",
        trading_pair: identity?.trading_pair ?? "",
        realized_pnl_quote: realized,
        unrealized_pnl_quote: unrealized,
        global_pnl_quote: net,
        global_pnl_pct: pct,
        volume_traded: volume,
        positions_summary: [],
      });
    }
  }
  return out;
}

// ── Sampling interval selection (PERF-238) ──

/**
 * The sampling intervals the controller-performance-history endpoint accepts,
 * finest first.
 *
 * This is not a guess and not the client docstring's abridged `"5m", "1h",
 * "1d"`: upstream validates the query parameter against the pattern
 * `^(5m|15m|30m|1h|4h|12h|1d)$` and answers 422 for anything else, so this
 * tuple is the whole accepted set and requesting a value outside it turns a
 * chart into an error, not a coarser chart.
 */
export const SAMPLING_INTERVALS = ["5m", "15m", "30m", "1h", "4h", "12h", "1d"] as const;

export type SamplingInterval = (typeof SAMPLING_INTERVALS)[number];

const SAMPLING_INTERVAL_MS: Record<SamplingInterval, number> = {
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "30m": 30 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "12h": 12 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

/**
 * One sampling bucket, in milliseconds.
 *
 * Takes a plain `string` because that is what comes back on the wire:
 * `ControllerPerformanceHistoryResponse.interval` is whatever the route
 * echoed, and a value outside `SAMPLING_INTERVALS` is a server that changed
 * under us rather than a caller mistake. Falling back to the finest interval
 * keeps every consumer conservative — an incremental refresh sizes its overlap
 * window from this, and a *too small* overlap is the one that loses a bucket.
 */
export function samplingIntervalMs(interval: string | undefined): number {
  return SAMPLING_INTERVAL_MS[interval as SamplingInterval] ?? SAMPLING_INTERVAL_MS["5m"];
}

/**
 * How many points one history query may ask for.
 *
 * Two independent limits happen to agree on this number, which is why it is a
 * good budget rather than a round one: the route caps `limit` at 1000
 * (CORR-260, `Query(1000, ge=1, le=1000)`), so a request for more points than
 * this cannot be answered in one page anyway; and the chart is drawn about a
 * thousand pixels wide, so a thousand points is already roughly one point per
 * column and everything beyond it is transferred, parsed, merged,
 * deep-compared and folded to land on a pixel that is already lit.
 */
export const HISTORY_POINT_BUDGET = 1000;

/**
 * Choose the finest sampling interval whose point count over `spanMs` fits the
 * budget.
 *
 * Both charts used to pin `"5m"` whatever span they asked for, so a fleet that
 * had been running a month requested 8,640 points per controller to draw a line
 * that ~720 hourly points draw identically — and then, because the route caps a
 * page at 1000 rows, actually got the first 1000 of them and silently drew a
 * partial history. Deriving the interval from how long the bots have really been
 * running is the cheapest possible reduction: it happens at the source, before
 * the bytes exist.
 *
 * Kept pure and span-shaped (rather than reading a deploy time and a clock) so
 * every threshold is a one-line test. With the default budget the ladder works
 * out to roughly: up to ~3.5d → 5m, ~10d → 15m, ~20d → 30m, ~41d → 1h,
 * ~166d → 4h, ~500d → 12h, beyond that → 1d.
 *
 * A span that is absent, zero, negative or not finite means "we do not know how
 * far back this goes", and the answer there is the finest interval — the
 * previous behaviour — not a coarse one: guessing coarse would throw away
 * detail for a bot that started ten minutes ago.
 */
export function pickSamplingInterval(
  spanMs: number | undefined,
  budget: number = HISTORY_POINT_BUDGET,
): SamplingInterval {
  if (spanMs === undefined || !Number.isFinite(spanMs) || spanMs <= 0) return "5m";
  for (const interval of SAMPLING_INTERVALS) {
    if (Math.ceil(spanMs / SAMPLING_INTERVAL_MS[interval]) <= budget) return interval;
  }
  return SAMPLING_INTERVALS[SAMPLING_INTERVALS.length - 1];
}

/**
 * The sampling interval for a history query that starts at `startTime`.
 *
 * The runtime comes from the data the callers already hold — a bot's
 * `deployed_at`, or the earliest `deployed_at` across the fleet — which is the
 * same value they pass as `start_time`, so the interval always describes the
 * window actually requested. An unparseable or missing start time falls back to
 * `"5m"` through `pickSamplingInterval`.
 */
export function samplingIntervalSince(
  startTime: string | null | undefined,
  now: number = Date.now(),
): SamplingInterval {
  if (!startTime) return "5m";
  const startMs = Date.parse(startTime);
  if (Number.isNaN(startMs)) return "5m";
  return pickSamplingInterval(now - startMs);
}

// ── Volume bar geometry (READ-245) ──

/**
 * The typical spacing between two adjacent points on a folded series, in ms —
 * the width one volume bar is meant to cover.
 *
 * It is the **median** gap, not the minimum and not the mean, and that is the
 * whole reason this function exists rather than the series being handed to
 * recharts as-is.
 *
 * On a numeric X axis recharts has no band to work from, so it derives a bar's
 * width from the *smallest* distance between two adjacent points
 * (`getBandSizeOfAxis` over the categorical domain), and an explicit `barSize`
 * is clamped back down to 0.9 of that — it can narrow a bar, never widen one.
 * That rule is fine for evenly spaced data, and this series is never quite
 * evenly spaced: the fold ends it with a live "now" point at `Date.now()`,
 * which lands a *fraction* of a bucket after the last stored snapshot. One gap
 * of a few seconds among hundreds a whole bucket wide would set the width of
 * every bar in the pane — so the bars would thin to a hairline and thicken
 * again as each new snapshot landed, on a loop.
 *
 * The median is what makes that one gap (and any other minority of odd ones —
 * a bucket the history is missing, a controller a beat out of step) count for
 * nothing, leaving the sampling interval the series was actually fetched at,
 * whichever rung of the PERF-238 ladder that is (`5m` … `1d`), without this
 * module having to be told which one. It is a *typical* spacing, not a
 * declared one: a merged timeline that genuinely ticks twice per bucket
 * reports the half-bucket it genuinely has, and the bars narrow to match
 * rather than overlapping each other.
 *
 * Returns 0 for a series too short to have a gap.
 */
export function chartBucketMs(data: PnlChartPoint[]): number {
  const gaps: number[] = [];
  for (let i = 1; i < data.length; i++) {
    const gap = data[i].time - data[i - 1].time;
    if (gap > 0) gaps.push(gap);
  }
  if (gaps.length === 0) return 0;
  gaps.sort((a, b) => a - b);
  return gaps[gaps.length >> 1];
}

/** Fraction of its bucket a bar fills; the rest is the gap that makes it a bar. */
export const VOLUME_BAR_DUTY = 0.72;
/** Below this a bar stops being visible; above it, bars on a short series look like blocks. */
export const VOLUME_BAR_MIN_PX = 1;
export const VOLUME_BAR_MAX_PX = 28;

/**
 * How wide, in px, to draw one volume bar — or `undefined` while that cannot
 * yet be known, in which case the caller leaves recharts to its own sizing.
 *
 * A bucket is `bucketMs` of a `spanMs` window drawn across `plotWidthPx`, so
 * its share of the plot is a plain proportion. Deriving it this way is what
 * makes the same code work at every rung of the sampling ladder: a 5m bucket
 * on a two-day window and a 1d bucket on a two-year one are the same fraction
 * of the axis and get the same bar.
 *
 * `plotWidthPx` is the *plot* width — the container minus both gutters and the
 * right margin — because that, not the card, is what the time domain is
 * stretched across.
 */
export function volumeBarWidth(
  plotWidthPx: number,
  spanMs: number,
  bucketMs: number,
): number | undefined {
  if (!(plotWidthPx > 0) || !(spanMs > 0) || !(bucketMs > 0)) return undefined;
  const ideal = (plotWidthPx * bucketMs) / spanMs;
  return Math.min(VOLUME_BAR_MAX_PX, Math.max(VOLUME_BAR_MIN_PX, ideal * VOLUME_BAR_DUTY));
}

/**
 * A sampling bucket rendered as the label the API uses for it — `"5m"`, `"1h"`,
 * `"1d"` — by snapping to the nearest rung of the PERF-238 ladder.
 *
 * The tooltip needs this because the number beside "Volume" changed meaning:
 * it used to be a running total, which needs no qualifier, and is now the
 * volume of one bucket, which is meaningless until you know how long a bucket
 * is. Snapping rather than formatting the raw median keeps the label the same
 * word the request used, and absorbs a series whose gaps are a second or two
 * off a round interval.
 *
 * Returns `undefined` when there is no bucket to name.
 */
export function formatBucketLabel(bucketMs: number): SamplingInterval | undefined {
  if (!(bucketMs > 0)) return undefined;
  let best: SamplingInterval = SAMPLING_INTERVALS[0];
  let bestDistance = Infinity;
  for (const interval of SAMPLING_INTERVALS) {
    // Compared in log space so "twice as long" counts the same whether the
    // rungs are minutes or days apart; a linear distance would snap almost
    // everything to "1d".
    const distance = Math.abs(Math.log(bucketMs / SAMPLING_INTERVAL_MS[interval]));
    if (distance < bestDistance) {
      bestDistance = distance;
      best = interval;
    }
  }
  return best;
}

// ── Zooming the loaded window (READ-249) ──

/**
 * A user's zoom over the loaded window, expressed in **time** rather than in
 * point indices — which is the whole design of this feature, not an
 * implementation detail.
 *
 * The series underneath a PNL chart is not static: the socket rebuilds it every
 * few seconds, a controller chip drops or restores a whole controller's
 * contribution, and widening the history window re-fetches it at a different
 * sampling interval. An index into that array means a different instant after
 * every one of those, so a selection stored as `[startIndex, endIndex]` would
 * slide, jump or point past the end. A pair of timestamps means the same
 * instants forever, and the worst a re-fetch can do to it is put those instants
 * outside the data, which `resolveTimeRange` answers by clamping.
 *
 * This is also why the chart does not use recharts' own `<Brush>`. recharts
 * keeps the brush as exactly those two indices in its store, and
 * `ChartDataContextProvider` resets them to the full range on *every* change of
 * the `data` prop's identity — cleanup dispatches `setChartData(undefined)`
 * (start and end to 0) and the re-run puts the end back at `length - 1`. On a
 * chart fed by a live socket that is a brush which silently opens itself every
 * few seconds, which is worse than no brush at all.
 *
 * The two nullable fields are what makes a selection able to *follow* the live
 * edge instead of being left behind by it:
 *
 *  - `end: null` — the window ends at the newest point there is, so points
 *    arriving after the selection was made are inside it.
 *  - `trailing` — the window keeps its width and slides, measured back from
 *    that live end. This is what the preset chips set ("the last hour" is a
 *    width, not a pair of instants) and what a drag that lands on the right
 *    edge is stored as.
 *
 * One rule follows from those, and it is the answer to "what happens to my
 * selection when new data arrives": **a selection touching the live edge keeps
 * its width and slides; a selection that does not is frozen where the user put
 * it.** Nothing resets.
 */
export interface TimeRange {
  /** Absolute window start, or null when `trailing` measures it from the end. */
  start: number | null;
  /** Absolute window end, or null for "the newest point there is". */
  end: number | null;
  /** Window width measured back from the end, in ms — a sliding window. */
  trailing?: number;
}

/** The one-click zoom levels offered beside the header stats. */
export const RANGE_PRESETS = [
  { label: "1h", ms: 3_600_000 },
  { label: "6h", ms: 6 * 3_600_000 },
  { label: "1d", ms: 24 * 3_600_000 },
] as const;

/** First and last instant on a series, or null when there is nothing to measure. */
export function seriesExtent(data: PnlChartPoint[]): [number, number] | null {
  if (data.length === 0) return null;
  return [data[0].time, data[data.length - 1].time];
}

/**
 * A selection resolved against the data actually loaded, as absolute
 * `[start, end]` instants.
 *
 * Everything that can go wrong with a stored selection is answered here rather
 * than at the call sites: a window that reaches past either end is clamped, and
 * one that no longer holds two points — a chip toggle that emptied it, a
 * re-fetch that moved the history somewhere else entirely — falls back to the
 * full loaded window rather than to a slice the panes cannot draw.
 */
export function resolveTimeRange(data: PnlChartPoint[], range: TimeRange | null): [number, number] {
  const extent = seriesExtent(data);
  if (!extent) return [0, 0];
  if (!range) return extent;
  const [first, last] = extent;
  const clamp = (t: number) => Math.min(Math.max(t, first), last);
  const end = clamp(range.end ?? last);
  const start = clamp(range.trailing != null ? end - range.trailing : (range.start ?? first));
  if (!(end > start)) return extent;
  let count = 0;
  for (const point of data) {
    if (point.time < start || point.time > end) continue;
    if (++count > 1) return [start, end];
  }
  return extent;
}

/**
 * The points inside `[start, end]` — and the *same array* when that is all of
 * them.
 *
 * Preserving identity for the unzoomed case is not a micro-optimisation: both
 * panes hand this straight to recharts as their `data`, and a fresh array on
 * every render re-dispatches the chart's data into its store, re-deriving every
 * axis domain each time. The default view has to cost exactly what it cost
 * before this feature existed.
 */
export function sliceToRange(data: PnlChartPoint[], start: number, end: number): PnlChartPoint[] {
  if (data.length === 0) return data;
  if (start <= data[0].time && end >= data[data.length - 1].time) return data;
  return data.filter((point) => point.time >= start && point.time <= end);
}

// ── Where a scope's PnL series comes from (FEAT-087) ──

import type { ControllerPerformanceSnapshot, PerformanceSnapshot } from "@/lib/api";
import type { ConvertFn } from "@/lib/rates";
import {
  aggregatePnlSeries,
  executorSeries,
  HISTORY_POINT_BUDGET,
  pickSamplingInterval,
  type ClosedOutcome,
  type PnlChartPoint,
  type SamplingInterval,
} from "@/lib/pnl-chart";

/**
 * The synthetic controller key a performance row folds under.
 *
 * `aggregatePnlSeries` groups by `controllerKey` — `bot_name:controller_id` —
 * and this route's rows have to land one group per *scope*, not one per
 * controller: two executors under the same controller are two series, and an
 * executor carries no bot name at all (a bot name is deliberately not a join
 * key between the two populations, so it is never invented here).
 *
 * `scope_id` is already exactly that scope — controller id for controllers,
 * executor id for executors — so it becomes the controller half of the key and
 * the bot half stays whatever the row actually carries. For a controller row
 * that reproduces the real composite; for an executor row it is `:<executor
 * id>`, which is unique because executor ids are.
 */
export function scopeKey(row: PerformanceSnapshot): string {
  return `${row.bot_name || ""}:${row.scope_id || row.executor_id || row.controller_id || ""}`;
}

/**
 * One performance row in the shape the fleet's fold already eats.
 *
 * The point of translating rather than teaching `aggregatePnlSeries` a second
 * input shape is that an executor's curve is then measured by *the same code*
 * that measures a controller's: the same forward-fill, the same per-scope
 * volume-delta clamp (READ-245), the same currency conversion through the row's
 * own pair. A second fold would be a second set of answers, and the two would
 * eventually disagree about the same executor.
 *
 * Three fields need saying out loud:
 *
 *  - **`realized`/`unrealized` are passed through untouched.** Upstream makes
 *    the split from settlement — unrealized while open, realized once closed,
 *    *except* `close_type === "POSITION_HOLD"`, which stays unrealized because
 *    the position was handed to `position_holds` and counting it as realized is
 *    the double-count that mapping exists to avoid. Re-deriving it here from
 *    `is_terminal` or from `status` would reintroduce exactly that bug, so this
 *    reads the two numbers and does no arithmetic on them.
 *  - **`volume_traded` is `volume_quote`.** One volume notion, on every
 *    executor type including LP, where it is the volume generated and
 *    deliberately not the capital deposited.
 *  - **`positions_summary` is empty.** These rows carry no position breakdown,
 *    and synthesising one from the PnL would draw a position nobody holds. The
 *    chart reads an all-zero position series as "no position series" and drops
 *    the pane, which is the honest rendering.
 */
export function asControllerSnapshot(row: PerformanceSnapshot): ControllerPerformanceSnapshot {
  return {
    timestamp: row.timestamp,
    bot_name: row.bot_name || "",
    controller_id: row.scope_id || row.executor_id || row.controller_id || "",
    controller_name: "",
    connector: row.connector_name || "",
    trading_pair: row.trading_pair || "",
    realized_pnl_quote: row.realized_pnl_quote,
    unrealized_pnl_quote: row.unrealized_pnl_quote,
    global_pnl_quote: row.global_pnl_quote,
    global_pnl_pct: row.global_pnl_pct,
    volume_traded: row.volume_quote,
    positions_summary: [],
  };
}

/**
 * The series a set of upstream performance rows draws.
 *
 * No live "now" point: `aggregatePnlSeries` appends one from the *controllers*
 * it is given, and these rows are the record — for an executor there is no
 * live controller to read a present value off, and for a scope that has closed
 * there is no "now" at all. The series ends where the snapshots end, which for
 * a live executor is at most one dump interval ago.
 */
export function snapshotSeries(
  rows: readonly PerformanceSnapshot[],
  convert?: ConvertFn,
): PnlChartPoint[] {
  if (!rows || rows.length === 0) return [];
  const mapped = rows.map(asControllerSnapshot);
  const keys = new Set(rows.map(scopeKey).filter(Boolean));
  return aggregatePnlSeries(mapped, keys, [], convert);
}

/**
 * The intervals a performance-history query may ask for.
 *
 * One rung finer than the client-side ladder, and that rung is the point.
 * `SAMPLING_INTERVALS` starts at `5m` because that is the *controller*
 * sampler's own grain — asking for anything finer could only return the same
 * rows. The executor series is written every 60s, and the route accepts `1m`,
 * so an executor that ran ten minutes is two points at `5m` and ten at `1m`.
 */
export type PerfInterval = "1m" | SamplingInterval;

/** One minute, in ms. The finest rung, and the executor dump cadence. */
const MINUTE_MS = 60_000;

/**
 * The sampling interval one scope's own history should be asked for.
 *
 * The same PERF-238 ladder the fleet history walks, sized from this scope's
 * span rather than the fleet's: an executor that ran four minutes and one that
 * ran four days are the same chart width, and the ladder is what keeps both
 * inside the point budget. `1m` is tried first and the shared ladder takes
 * over the moment a minute's resolution would overrun that budget, so the two
 * kinds of series are still drawn at comparable densities.
 *
 * A running scope is measured to `now`; a finished one to its close, so its
 * interval stops changing once it has stopped trading — which is also what
 * keeps a closed executor's query key stable and its cache entry warm.
 *
 * `now` is a default parameter rather than a call in the caller's `useMemo`,
 * for the reason `samplingIntervalSince` does the same: reading the clock
 * during render is impure, and a component that re-renders would silently
 * change the resolution of a chart nobody touched.
 */
export function scopeInterval(
  startedAt: number | null | undefined,
  endedAt: number | null | undefined,
  now: number = Date.now(),
): PerfInterval {
  // A scope that does not say when it started has no span to size from. The
  // finest rung is right for the case that produces this — a young executor.
  if (!startedAt) return "1m";
  const span = (endedAt ?? now) - startedAt;
  if (span <= 0) return "1m";
  if (Math.ceil(span / MINUTE_MS) <= HISTORY_POINT_BUDGET) return "1m";
  return pickSamplingInterval(span);
}

/** Whether a row set says anything about fees, as opposed to saying zero. */
export function feesAreKnown(rows: readonly PerformanceSnapshot[]): boolean {
  return rows.some((row) => row.cum_fees_quote !== null && row.cum_fees_quote !== undefined);
}

/**
 * Cumulative fees over a row set, or `null` when nothing measured them.
 *
 * Controllers report `cum_fees_quote: null` — their `PerformanceReport`
 * genuinely has no fees field — and `null` is not zero. A caller that folded
 * these with `?? 0` would draw a controller as having traded for free, which is
 * a stronger claim than the data makes, so the absence propagates instead.
 */
export function cumulativeFees(rows: readonly PerformanceSnapshot[]): number | null {
  if (!feesAreKnown(rows)) return null;
  // The newest row per scope carries that scope's running total; summing every
  // row would count each dump again.
  const newest = new Map<string, PerformanceSnapshot>();
  for (const row of rows) {
    if (row.cum_fees_quote === null || row.cum_fees_quote === undefined) continue;
    const key = scopeKey(row);
    const seen = newest.get(key);
    if (!seen || Date.parse(row.timestamp) >= Date.parse(seen.timestamp)) newest.set(key, row);
  }
  let total = 0;
  for (const row of newest.values()) total += row.cum_fees_quote ?? 0;
  return total;
}

/** Which of the three sources actually drew the series. */
export type PerfSeriesSource =
  /** Upstream `/performance/history` rows — the real sampled curve. */
  | "snapshots"
  /** The controller history the page already walked. */
  | "controller-history"
  /** Close times and final PnLs, summed. Exact for a closed set, silent for a live one. */
  | "closed-outcomes"
  /** Nothing could be drawn. */
  | "none";

export interface PerfSeriesResult {
  points: PnlChartPoint[];
  source: PerfSeriesSource;
  /**
   * True when the fallback was taken *because this server has no
   * `/performance/history`*, as opposed to because the scope genuinely has no
   * sampled history. The chart's notice says which, so a reader looking at a
   * derived curve can tell whether the fix is upgrading their API.
   */
  unsupported: boolean;
}

export interface PerfSeriesInput {
  /** Rows from `/performance/history` for this scope, if any were fetched. */
  snapshots?: readonly PerformanceSnapshot[];
  /** The controller-history fold this scope would otherwise draw. */
  controllerPoints?: PnlChartPoint[];
  /** The closed records this scope holds, for the derived fallback. */
  outcomes?: readonly ClosedOutcome[];
  /**
   * What the capability probe said. `undefined` means it has not answered yet
   * — treated as "not known to be missing", so a chart does not flash a
   * "your API is older" notice while the probe is still in flight.
   */
  supported?: boolean;
  /** Quote conversion for the snapshot fold, which resolves a pair's quote itself. */
  convert?: ConvertFn;
  /** Quote conversion for the outcome fold, which is handed a whole pair. */
  cv?: (value: number, pair: string) => number;
}

/**
 * The one place that resolves "the series for this scope".
 *
 * [[FEAT-086]] left this decision inline in `PerfBrowser`, spread across a
 * `useMemo` and two notices, and it had a fourth branch that this function
 * deliberately does not: a *running* executor borrowing its parent controller's
 * curve. That was the only thing available when an executor was one mutable row
 * upstream — and it drew a controller's line under an executor's name, which
 * is the same picture asserting something false. With `/performance/history` a
 * running executor has a curve of its own; without it, it has none, and saying
 * so is better than borrowing one.
 *
 * The order is not a preference list, it is a strength ordering:
 *
 *  1. **Upstream snapshots.** The real sampled series, for either population.
 *  2. **The controller history** the page already walked, for a controller,
 *     bot or fleet scope. Same data, one route older.
 *  3. **Closed outcomes.** Exact for a terminated set — a close is a known
 *     value at a known instant — and empty for anything still running, which
 *     is why it cannot be first.
 *
 * `unsupported` rides along rather than being re-derived by the caller: it is
 * the difference between "this scope has no history" and "this server cannot
 * tell you", and only this function has both facts in hand.
 */
export function resolvePerfSeries(input: PerfSeriesInput): PerfSeriesResult {
  const { snapshots, controllerPoints, outcomes, supported, convert, cv } = input;
  // `supported === undefined` is the probe still in flight, not a "no": a chart
  // must not accuse a server of being out of date before it has been asked.
  const unsupported = supported === false;

  if (snapshots && snapshots.length > 0) {
    const points = snapshotSeries(snapshots, convert);
    if (points.length > 0) return { points, source: "snapshots", unsupported: false };
  }

  if (controllerPoints && controllerPoints.length > 0) {
    return { points: controllerPoints, source: "controller-history", unsupported };
  }

  if (outcomes && outcomes.length > 0) {
    const points = executorSeries(outcomes, cv ?? ((value) => value));
    if (points.length > 0) return { points, source: "closed-outcomes", unsupported };
  }

  return { points: [], source: "none", unsupported };
}

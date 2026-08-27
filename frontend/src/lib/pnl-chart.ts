// ── Shared helpers for PNL evolution charts ──

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { toMs } from "@/lib/formatters";
import type { ConvertFn } from "@/lib/rates";

/**
 * Fixed series colors shared by strokes, axis ticks, header stats and tooltips
 * across AggregatedPnlChart and ControllerPnlChart. Realized and total are
 * theme-driven (getThemeColors / pnlColor), so only the three fixed series live here.
 */
export const PNL_SERIES_COLORS = {
  unrealized: "#f59e0b",
  volume: "#3b82f6",
  position: "#a78bfa",
} as const;

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

/** A single point on a PNL evolution chart (per-controller or aggregated). */
export interface PnlChartPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  volume: number;
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
 */
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

  const byCtrl: Record<string, ControllerPerformanceSnapshot[]> = {};
  for (const snap of snapshots) {
    const key = controllerKey(snap);
    if (!key || !enabledIds.has(key)) continue;
    (byCtrl[key] ??= []).push(snap);
  }

  for (const snaps of Object.values(byCtrl)) {
    snaps.sort((a, b) => toMs(a.timestamp) - toMs(b.timestamp));
  }

  const timeSet = new Set<number>();
  for (const snaps of Object.values(byCtrl))
    for (const s of snaps) timeSet.add(toMs(s.timestamp));
  const times = Array.from(timeSet).sort((a, b) => a - b);
  if (times.length === 0) return [];

  const cids = Object.keys(byCtrl);
  const cursors: Record<string, number> = {};
  for (const c of cids) cursors[c] = 0;

  const points: PnlChartPoint[] = [];
  for (const t of times) {
    let realized = 0, unrealized = 0, volume = 0, position = 0;
    for (const cid of cids) {
      const snaps = byCtrl[cid];
      while (cursors[cid] < snaps.length - 1 && toMs(snaps[cursors[cid] + 1].timestamp) <= t)
        cursors[cid]++;
      if (toMs(snaps[cursors[cid]].timestamp) <= t) {
        const s = snaps[cursors[cid]];
        const pair = s.trading_pair || pairByCtrl[cid] || "";
        realized += cv(s.realized_pnl_quote, pair);
        unrealized += cv(s.unrealized_pnl_quote, pair);
        volume += cv(s.volume_traded, pair);
        if (Array.isArray(s.positions_summary)) {
          position += cv(positionQuoteValue(s.positions_summary as Record<string, unknown>[]), pair);
        }
      }
    }
    points.push({ time: t, realized, unrealized, total: realized + unrealized, volume, position });
  }

  // Append a live "now" point from controllers so the graph ends at real-time values
  const now = Date.now();
  let liveRealized = 0, liveUnrealized = 0, liveVolume = 0, livePosition = 0;
  let hasLive = false;
  for (const ctrl of controllers) {
    const cid = controllerKey(ctrl);
    if (!cid || !enabledIds.has(cid)) continue;
    hasLive = true;
    const pair = ctrl.trading_pair || "";
    liveRealized += cv(ctrl.realized_pnl_quote, pair);
    liveUnrealized += cv(ctrl.unrealized_pnl_quote, pair);
    liveVolume += cv(ctrl.volume_traded, pair);
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
      position: livePosition,
    });
  }

  return points;
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

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

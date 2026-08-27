// ── The PNL evolution chart: header strip + PNL pane + volume/position pane ──
//
// One component draws every PNL evolution chart in the app (ARCH-242). Before
// this, AggregatedPnlChart (the bots page) and ControllerPnlChart (the
// controller modal) each carried their own byte-for-byte copy of the same ~100
// lines of recharts JSX, and the two had already drifted apart — the controller
// header never showed the Pos stat its own bottom pane was drawing.
//
// Those two callers now own only what is genuinely theirs: where the points
// come from (a live aggregation over enabled controllers vs. a single
// controller's history query), and what sits in the filter row. Everything you
// can see — the header, the gradient, the axes, the tooltips, the legend, the
// series — lives here, once.
//
// Two invariants this file owns, both of which fail *silently* if broken:
//
//  1. Gutters. The two panes are separate charts tied together only by
//     `syncId`, which syncs the cursor and the tooltip index but never the
//     geometry (see AXIS_WIDTH in lib/pnl-chart.ts). Each pane derives its plot
//     area from the container width minus its own gutters, so a given instant
//     sits in the same column in both panes only while their gutters match.
//     Both panes therefore render AXIS_WIDTH on the left *and* AXIS_WIDTH on
//     the right, unconditionally — the right-hand axis is always there, and
//     only its ticks come and go with the data. Add an axis to one pane and you
//     must add its mirror to the other.
//
//  2. Identity. The gradient element id and the `syncId` are derived per
//     instance from useId(), not hardcoded. ControllerBrowser's modal opens
//     over the still-mounted aggregated chart, so a fixed id would make the
//     second instance's area resolve to the first instance's gradient (and
//     cross-sync the two charts' tooltips) the moment both are on the page.

import { useCallback, useId, type ReactNode } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatAxisCurrency, formatCurrencyVolume, formatCurrencyPnl, formatTime, pnlColor } from "@/lib/formatters";
import { AXIS_WIDTH, PNL_SERIES_COLORS, type PnlChartPoint } from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";
import { BottomTooltip, PnlTooltip } from "./PnlChartTooltips";

interface Props {
  /** The timeline both panes draw. Callers own how it is built and when it is empty. */
  data: PnlChartPoint[];
  /** Header label, e.g. "Portfolio PnL" or "PnL Evolution". */
  title: string;
  /** Height of the PNL pane, px. */
  pnlHeight: number;
  /** Height of the volume/position pane, px. */
  volumeHeight: number;
  /** Currency the values are already expressed in — used for ticks, stats and tooltips. */
  currencySymbol?: string;
  /** Optional row between the header and the panes (the aggregated chart's controller chips). */
  filters?: ReactNode;
}

export function PnlEvolutionChart({ data, title, pnlHeight, volumeHeight, currencySymbol = "$", filters }: Props) {
  // Unique per mounted instance: two charts on one page must not share a
  // gradient element or a sync group. useId() emits colons, which are legal in
  // an id but awkward in selectors, so strip them.
  const instanceId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const gradientId = `pnlGrad${instanceId}`;

  // recharts compares `tickFormatter` by identity, so these stay stable per symbol.
  const fmtAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "pnl"), [currencySymbol]);
  const fmtVolAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "volume"), [currencySymbol]);

  // Latest point is the live "now" point appended by aggregatePnlSeries
  const latest = data.length > 0 ? data[data.length - 1] : null;
  const hasPosition = data.some((p) => p.position !== 0);

  const tc = getThemeColors();
  const totalColor = (latest?.total ?? 0) >= 0 ? tc.up : tc.down;
  const fmtPnl = (v: number) => formatCurrencyPnl(v, currencySymbol);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {/* Header with live stats */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex items-center gap-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            {title}
          </p>
          {latest && (
            <div className="flex items-center gap-3 text-xs tabular-nums">
              <span style={{ color: pnlColor(latest.total) }} className="font-semibold">
                {fmtPnl(latest.total)}
              </span>
              <span className="text-[var(--color-text-muted)]">
                R: <span style={{ color: "var(--color-green)" }}>{fmtPnl(latest.realized)}</span>
              </span>
              <span className="text-[var(--color-text-muted)]">
                U: <span style={{ color: PNL_SERIES_COLORS.unrealized }}>{fmtPnl(latest.unrealized)}</span>
              </span>
              <span className="text-[var(--color-text-muted)]">
                Vol: <span style={{ color: PNL_SERIES_COLORS.volume }}>{formatCurrencyVolume(latest.volume, currencySymbol)}</span>
              </span>
              {latest.position !== 0 && (
                <span className="text-[var(--color-text-muted)]">
                  Pos: <span style={{ color: PNL_SERIES_COLORS.position }}>{formatCurrencyVolume(latest.position, currencySymbol)}</span>
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {filters}

      {/* PnL pane (top) */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={pnlHeight}>
          <ComposedChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} syncId={instanceId}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={totalColor} stopOpacity={0.15} />
                <stop offset="95%" stopColor={totalColor} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
            <XAxis
              dataKey="time"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatTime}
              tick={false}
              stroke="var(--color-border)"
              tickLine={false}
              height={1}
            />
            <YAxis
              tickFormatter={fmtAxis}
              tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            {/* Empty right gutter mirroring the bottom pane's position axis so
                both panes keep identical plot areas — invariant 1 above. */}
            <YAxis
              yAxisId="spacer"
              orientation="right"
              tick={false}
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            <ReferenceLine y={0} stroke="var(--color-text-muted)" strokeOpacity={0.3} strokeDasharray="4 4" />
            <Tooltip content={<PnlTooltip symbol={currencySymbol} />} />
            <Area type="monotone" dataKey="total" stroke="none" fill={`url(#${gradientId})`} activeDot={false} legendType="none" />
            <Line type="monotone" dataKey="total" stroke={totalColor} strokeWidth={2} dot={false} strokeOpacity={0.6} />
            <Line type="monotone" dataKey="realized" stroke={tc.up} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="unrealized" stroke={PNL_SERIES_COLORS.unrealized} strokeWidth={2} strokeDasharray="5 3" dot={false} />
            <Legend
              verticalAlign="top"
              align="right"
              iconType="plainline"
              wrapperStyle={{ fontSize: 10, paddingBottom: 4 }}
              formatter={(value: string) => <span className="text-[var(--color-text-muted)] text-[10px] capitalize">{value}</span>}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Volume + Position pane (bottom) */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={volumeHeight}>
          <ComposedChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }} syncId={instanceId}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
            <XAxis
              dataKey="time"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatTime}
              tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
              stroke="var(--color-border)"
              tickLine={false}
            />
            <YAxis
              yAxisId="vol"
              tickFormatter={fmtVolAxis}
              tick={{ fontSize: 10, fill: PNL_SERIES_COLORS.volume }}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            {/* Always rendered so the gutters match the pane above; only its
                ticks depend on whether there is a position to label. */}
            <YAxis
              yAxisId="pos"
              orientation="right"
              tickFormatter={fmtVolAxis}
              tick={hasPosition ? { fontSize: 10, fill: PNL_SERIES_COLORS.position } : false}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            <Tooltip content={<BottomTooltip symbol={currencySymbol} />} />
            <Line yAxisId="vol" type="monotone" dataKey="volume" stroke={PNL_SERIES_COLORS.volume} strokeWidth={1.5} dot={false} />
            {hasPosition && (
              <Line yAxisId="pos" type="monotone" dataKey="position" stroke={PNL_SERIES_COLORS.position} strokeWidth={1.5} dot={false} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

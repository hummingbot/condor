import { useMemo, useState } from "react";
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

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { formatCurrencyVolume, formatCurrencyPnl, formatTime, pnlColor } from "@/lib/formatters";
import { aggregatePnlSeries, PNL_SERIES_COLORS } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";
import { getThemeColors } from "@/lib/theme-colors";
import { BottomTooltip, PnlTooltip } from "./PnlChartTooltips";

// ── Controller color palette ──

const CTRL_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a78bfa", "#ec4899", "#14b8a6", "#f97316"];

// ── Main component ──

interface Props {
  snapshots: ControllerPerformanceSnapshot[];
  controllers: ControllerInfo[];
  currencySymbol?: string;
  convert?: ConvertFn;
}

export function AggregatedPnlChart({ snapshots, controllers, currencySymbol = "$", convert }: Props) {
  const controllerIds = useMemo(() => {
    const ids: { id: string }[] = [];
    const seen = new Set<string>();
    for (const c of controllers) {
      const cid = c.controller_id || c.controller_name;
      if (!seen.has(cid)) {
        seen.add(cid);
        ids.push({ id: cid });
      }
    }
    return ids;
  }, [controllers]);

  const [enabled, setEnabled] = useState<Set<string>>(() => new Set(controllerIds.map((c) => c.id)));

  // Sync when controllers change
  useMemo(() => {
    const allIds = new Set(controllerIds.map((c) => c.id));
    setEnabled((prev) => {
      const next = new Set(prev);
      for (const id of prev) {
        if (!allIds.has(id)) next.delete(id);
      }
      if (next.size === 0) return allIds;
      return next;
    });
  }, [controllerIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleController = (id: string) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        if (next.size === 0) return prev;
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const allEnabled = enabled.size === controllerIds.length;
  const toggleAll = () => {
    if (allEnabled) return;
    setEnabled(new Set(controllerIds.map((c) => c.id)));
  };

  const data = useMemo(
    () => aggregatePnlSeries(snapshots, enabled, controllers, convert),
    [snapshots, enabled, controllers, convert],
  );
  // Latest point is the live "now" point appended by aggregatePnlSeries
  const latest = data.length > 0 ? data[data.length - 1] : null;
  const hasPosition = data.some((p) => p.position !== 0);

  if (!snapshots || snapshots.length === 0 || data.length < 2) return null;

  const tc = getThemeColors();
  const totalColor = (latest?.total ?? 0) >= 0 ? tc.up : tc.down;
  const fmtPnl = (v: number) => formatCurrencyPnl(v, currencySymbol);
  const fmtAxis = (v: number) => `${currencySymbol}${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(Math.abs(v) < 10 ? 2 : 0)}`;
  const fmtVolAxis = (v: number) => `${currencySymbol}${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(0)}`;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {/* Header with live stats */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex items-center gap-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Portfolio PnL
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

      {/* Controller filter chips */}
      {controllerIds.length > 1 && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-bg)] overflow-x-auto">
          <button
            onClick={toggleAll}
            className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-colors whitespace-nowrap ${
              allEnabled
                ? "bg-[var(--color-text-muted)]/20 text-[var(--color-text)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            All
          </button>
          {controllerIds.map((c, i) => {
            const color = CTRL_COLORS[i % CTRL_COLORS.length];
            const active = enabled.has(c.id);
            return (
              <button
                key={c.id}
                onClick={() => toggleController(c.id)}
                className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-all whitespace-nowrap ${
                  active ? "text-white" : "opacity-40 hover:opacity-70"
                }`}
                style={{
                  backgroundColor: active ? color : "transparent",
                  border: `1px solid ${color}`,
                  color: active ? "white" : color,
                }}
              >
                {c.id}
              </button>
            );
          })}
        </div>
      )}

      {/* PnL chart (top) */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} syncId="agg">
            <defs>
              <linearGradient id="aggPnlGrad" x1="0" y1="0" x2="0" y2="1">
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
              width={52}
            />
            {hasPosition && (
              <YAxis
                yAxisId="spacer"
                orientation="right"
                tick={false}
                tickLine={false}
                axisLine={false}
                width={52}
              />
            )}
            <ReferenceLine y={0} stroke="var(--color-text-muted)" strokeOpacity={0.3} strokeDasharray="4 4" />
            <Tooltip content={<PnlTooltip symbol={currencySymbol} />} />
            <Area type="monotone" dataKey="total" stroke="none" fill="url(#aggPnlGrad)" activeDot={false} legendType="none" />
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

      {/* Volume + Position chart (bottom) */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={120}>
          <ComposedChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }} syncId="agg">
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
              width={52}
            />
            {hasPosition && (
              <YAxis
                yAxisId="pos"
                orientation="right"
                tickFormatter={fmtVolAxis}
                tick={{ fontSize: 10, fill: PNL_SERIES_COLORS.position }}
                stroke="var(--color-border)"
                tickLine={false}
                axisLine={false}
                width={52}
              />
            )}
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

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
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

import { api, type ControllerInfo } from "@/lib/api";
import { formatCurrencyVolume, formatCurrencyPnl, formatTime, pnlColor } from "@/lib/formatters";
import { aggregatePnlSeries, PNL_SERIES_COLORS } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";
import { getThemeColors } from "@/lib/theme-colors";
import { BottomTooltip, PnlTooltip } from "./PnlChartTooltips";

// ── Component ──

interface Props {
  server: string;
  controllerId: string;
  botName: string;
  deployedAt?: string | null;
  height?: number;
  currencySymbol?: string;
  convert?: ConvertFn;
  controller?: ControllerInfo;
}

export function ControllerPnlChart({ server, controllerId, botName, deployedAt, height = 400, currencySymbol = "$", convert, controller }: Props) {
  const { data: raw, isLoading } = useQuery({
    queryKey: ["controller-perf-history", server, controllerId, deployedAt],
    queryFn: () =>
      api.getControllerPerformanceHistory(server, {
        controller_id: controllerId,
        bot_name: botName,
        interval: "5m",
        limit: 1000,
        start_time: deployedAt ?? undefined,
      }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const snapshots = raw?.snapshots ?? [];

  // One controller is the degenerate case of the aggregate fold: a single
  // enabled id, so the shared function does the sorting, the conversion and the
  // live "now" point (ARCH-243) instead of a second hand-rolled copy here.
  const data = useMemo(
    () => aggregatePnlSeries(snapshots, new Set([controllerId]), controller ? [controller] : [], convert),
    [snapshots, controllerId, controller, convert],
  );
  const hasPosition = data.some((p) => p.position !== 0);
  const latest = data.length > 0 ? data[data.length - 1] : null;

  if (isLoading) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center" style={{ height }}>
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Loading performance history...
        </div>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center" style={{ height }}>
        <p className="text-xs text-[var(--color-text-muted)]">No performance history available</p>
      </div>
    );
  }

  const tc = getThemeColors();
  const totalColor = (latest?.total ?? 0) >= 0 ? tc.up : tc.down;
  const pnlH = Math.round(height * 0.65);
  const bottomH = height - pnlH;
  const fmtPnl = (v: number) => formatCurrencyPnl(v, currencySymbol);
  const fmtAxis = (v: number) => `${currencySymbol}${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(Math.abs(v) < 10 ? 2 : 0)}`;
  const fmtVolAxis = (v: number) => `${currencySymbol}${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(0)}`;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {/* Header with live stats */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          PnL Evolution
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
          </div>
        )}
      </div>

      {/* PnL chart */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={pnlH}>
          <ComposedChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} syncId="ctrl">
            <defs>
              <linearGradient id="ctrlPnlGrad" x1="0" y1="0" x2="0" y2="1">
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
            <Area type="monotone" dataKey="total" stroke="none" fill="url(#ctrlPnlGrad)" activeDot={false} legendType="none" />
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

      {/* Volume + Position chart */}
      <div className="px-1">
        <ResponsiveContainer width="100%" height={bottomH}>
          <ComposedChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }} syncId="ctrl">
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

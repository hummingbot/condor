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
import { formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";

function toMs(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? ts : ts * 1000;
  const parsed = Date.parse(ts);
  return isNaN(parsed) ? 0 : parsed;
}

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(ms: number): string {
  const d = new Date(ms);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" })}`;
}

/** Compute net position value in quote from positions_summary */
function positionQuoteValue(positions: Record<string, unknown>[]): number {
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

interface DataPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  volume: number;
  position: number;
}

// ── Tooltips ──

function PnlTooltip({ active, payload, label, symbol }: {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number }>;
  label?: number;
  symbol: string;
}) {
  if (!active || !payload?.length || !label) return null;
  const byKey: Record<string, number> = {};
  for (const p of payload) byKey[p.dataKey] = p.value;
  const total = byKey.total ?? (byKey.realized ?? 0) + (byKey.unrealized ?? 0);
  const sign = (v: number) => (v >= 0 ? "+" : "");

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[150px]">
      <div className="text-[var(--color-text-muted)] text-[10px] mb-1">{formatDateTime(label)}</div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">Total</span>
        <span className="font-semibold" style={{ color: pnlColor(total) }}>
          {sign(total)}{formatCurrencyVolume(total, symbol)}
        </span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">Realized</span>
        <span style={{ color: "var(--color-green)" }}>{sign(byKey.realized ?? 0)}{formatCurrencyVolume(byKey.realized ?? 0, symbol)}</span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">Unrealized</span>
        <span style={{ color: "#f59e0b" }}>{sign(byKey.unrealized ?? 0)}{formatCurrencyVolume(byKey.unrealized ?? 0, symbol)}</span>
      </div>
    </div>
  );
}

function BottomTooltip({ active, payload, label, symbol }: {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number }>;
  label?: number;
  symbol: string;
}) {
  if (!active || !payload?.length || !label) return null;
  const byKey: Record<string, number> = {};
  for (const p of payload) byKey[p.dataKey] = p.value;

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[130px]">
      <div className="text-[var(--color-text-muted)] text-[10px] mb-1">{formatDateTime(label)}</div>
      <div className="flex justify-between gap-3">
        <span style={{ color: "#3b82f6" }}>Volume</span>
        <span style={{ color: "#3b82f6" }}>{formatCurrencyVolume(byKey.volume ?? 0, symbol)}</span>
      </div>
      {byKey.position !== undefined && byKey.position !== 0 && (
        <div className="flex justify-between gap-3">
          <span style={{ color: "#a78bfa" }}>Position</span>
          <span style={{ color: "#a78bfa" }}>{formatCurrencyVolume(byKey.position, symbol)}</span>
        </div>
      )}
    </div>
  );
}

// ── Component ──

type ConvertFn = (value: number, quoteCurrency: string) => { value: number; converted: boolean };

interface Props {
  server: string;
  controllerId: string;
  botName: string;
  deployedAt?: string | null;
  height?: number;
  currencySymbol?: string;
  tradingPair?: string;
  convert?: ConvertFn;
  controller?: ControllerInfo;
}

export function ControllerPnlChart({ server, controllerId, botName, deployedAt, height = 400, currencySymbol = "$", tradingPair, convert, controller }: Props) {
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

  const { data, hasPosition, latest } = useMemo(() => {
    if (snapshots.length === 0) return { data: [], hasPosition: false, latest: null };

    const quote = tradingPair?.split("-")[1] || "USDT";
    const cv = (val: number) => convert ? convert(val, quote).value : val;

    const sorted = [...snapshots].sort((a, b) => toMs(a.timestamp) - toMs(b.timestamp));
    let hasPos = false;

    const pts: DataPoint[] = sorted.map((s) => {
      let posValue = 0;
      if (s.positions_summary) {
        posValue = positionQuoteValue(s.positions_summary as Record<string, unknown>[]);
      }
      if (posValue !== 0) hasPos = true;

      return {
        time: toMs(s.timestamp),
        realized: cv(s.realized_pnl_quote),
        unrealized: cv(s.unrealized_pnl_quote),
        total: cv(s.realized_pnl_quote + s.unrealized_pnl_quote),
        volume: cv(s.volume_traded),
        position: cv(posValue),
      };
    });

    // Append live "now" point from controller so graph ends at real-time values
    if (controller) {
      let livePos = 0;
      if (Array.isArray(controller.positions_summary)) {
        livePos = positionQuoteValue(controller.positions_summary as Record<string, unknown>[]);
      }
      if (livePos !== 0) hasPos = true;
      pts.push({
        time: Date.now(),
        realized: cv(controller.realized_pnl_quote),
        unrealized: cv(controller.unrealized_pnl_quote),
        total: cv(controller.realized_pnl_quote + controller.unrealized_pnl_quote),
        volume: cv(controller.volume_traded),
        position: cv(livePos),
      });
    }

    return { data: pts, hasPosition: hasPos, latest: pts[pts.length - 1] || null };
  }, [snapshots, convert, tradingPair, controller]);

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
              U: <span style={{ color: "#f59e0b" }}>{fmtPnl(latest.unrealized)}</span>
            </span>
            <span className="text-[var(--color-text-muted)]">
              Vol: <span style={{ color: "#3b82f6" }}>{formatCurrencyVolume(latest.volume, currencySymbol)}</span>
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
                <stop offset="5%" stopColor={(latest?.total ?? 0) >= 0 ? "#22c55e" : "#ef4444"} stopOpacity={0.15} />
                <stop offset="95%" stopColor={(latest?.total ?? 0) >= 0 ? "#22c55e" : "#ef4444"} stopOpacity={0.02} />
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
            <Line type="monotone" dataKey="total" stroke={(latest?.total ?? 0) >= 0 ? "#22c55e" : "#ef4444"} strokeWidth={2} dot={false} strokeOpacity={0.6} />
            <Line type="monotone" dataKey="realized" stroke="#22c55e" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="unrealized" stroke="#f59e0b" strokeWidth={2} strokeDasharray="5 3" dot={false} />
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
              tick={{ fontSize: 10, fill: "#3b82f6" }}
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
                tick={{ fontSize: 10, fill: "#a78bfa" }}
                stroke="var(--color-border)"
                tickLine={false}
                axisLine={false}
                width={52}
              />
            )}
            <Tooltip content={<BottomTooltip symbol={currencySymbol} />} />
            <Line yAxisId="vol" type="monotone" dataKey="volume" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
            {hasPosition && (
              <Line yAxisId="pos" type="monotone" dataKey="position" stroke="#a78bfa" strokeWidth={1.5} dot={false} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

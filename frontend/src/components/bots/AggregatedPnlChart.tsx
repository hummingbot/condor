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
import { formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";

// ── Helpers ──

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

// ── Aggregation ──

type ConvertFn = (value: number, quoteCurrency: string) => { value: number; converted: boolean };

interface AggPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  volume: number;
  position: number;
}

function aggregate(
  snapshots: ControllerPerformanceSnapshot[],
  enabledIds: Set<string>,
  controllers: ControllerInfo[],
  convertFn?: ConvertFn,
): AggPoint[] {
  if (!snapshots || snapshots.length === 0) return [];

  // Build a lookup from controller id -> trading_pair using live controller data
  const pairByCtrl: Record<string, string> = {};
  for (const ctrl of controllers) {
    const cid = ctrl.controller_id || ctrl.controller_name;
    if (ctrl.trading_pair) pairByCtrl[cid] = ctrl.trading_pair;
  }

  const cv = (val: number, pair: string) => {
    if (!convertFn) return val;
    const quote = pair?.split("-")[1] || "USDT";
    return convertFn(val, quote).value;
  };

  const byCtrl: Record<string, ControllerPerformanceSnapshot[]> = {};
  for (const snap of snapshots) {
    const key = snap.controller_id || snap.controller_name;
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

  const points: AggPoint[] = [];
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
    const cid = ctrl.controller_id || ctrl.controller_name;
    if (!enabledIds.has(cid)) continue;
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

// ── Custom tooltips ──

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
    () => aggregate(snapshots, enabled, controllers, convert),
    [snapshots, enabled, controllers, convert],
  );
  // Latest point is the live "now" point appended by aggregate
  const latest = data.length > 0 ? data[data.length - 1] : null;
  const hasPosition = data.some((p) => p.position !== 0);

  if (!snapshots || snapshots.length === 0 || data.length < 2) return null;

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
                U: <span style={{ color: "#f59e0b" }}>{fmtPnl(latest.unrealized)}</span>
              </span>
              <span className="text-[var(--color-text-muted)]">
                Vol: <span style={{ color: "#3b82f6" }}>{formatCurrencyVolume(latest.volume, currencySymbol)}</span>
              </span>
              {latest.position !== 0 && (
                <span className="text-[var(--color-text-muted)]">
                  Pos: <span style={{ color: "#a78bfa" }}>{formatCurrencyVolume(latest.position, currencySymbol)}</span>
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
            <Area type="monotone" dataKey="total" stroke="none" fill="url(#aggPnlGrad)" activeDot={false} legendType="none" />
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

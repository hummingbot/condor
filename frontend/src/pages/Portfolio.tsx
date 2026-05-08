import { useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Wallet,
  Server,
  BarChart3,
  Layers,
  KeyRound,
  ArrowUpRight,
  ArrowDownRight,
} from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useServer } from "@/hooks/useServer";
import {
  api,
  type AgentSummary,
  type BalanceItem,
  type ConnectorBalance,
  type PortfolioHistoryPoint,
  type PortfolioHistoryResponse,
} from "@/lib/api";

// ── Formatters ──

function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function formatPrice(val: number) {
  if (val === 0) return "-";
  if (val >= 1000) return "$" + val.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (val >= 1) return "$" + val.toFixed(2);
  if (val >= 0.01) return "$" + val.toFixed(4);
  return "$" + val.toExponential(2);
}

function formatAmount(val: number) {
  if (val === 0) return "0";
  if (Math.abs(val) >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return (val / 1_000).toFixed(1) + "K";
  if (Math.abs(val) < 0.001) return val.toExponential(2);
  return val.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function formatPnl(val: number) {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatUsd(val);
}

// ── Chart Colors ──

const CHART_COLORS = [
  "#d4a845", // gold
  "#22c55e", // green
  "#6366f1", // indigo
  "#ef4444", // red
  "#06b6d4", // cyan
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#14b8a6", // teal
  "#f97316", // orange
  "#a78bfa", // light violet
];

// ── KPI helpers ──

const TIME_PERIODS = ["1D", "1W", "1M"] as const;

function isExecutorActive(status: string) {
  return status === "active" || status === "running";
}

function computeExecutorStats(executors: import("@/lib/api").ExecutorInfo[], period: string) {
  const now = Date.now() / 1000;
  const cutoff =
    period === "1D" ? now - 86400 :
    period === "1W" ? now - 7 * 86400 :
    now - 30 * 86400;

  const filtered = executors.filter((e) => e.timestamp >= cutoff);
  const pnl = filtered.reduce((s, e) => s + e.pnl, 0);
  const volume = filtered.reduce((s, e) => s + e.volume, 0);
  const count = filtered.length;
  return { pnl, volume, count };
}

// ── Unified Dashboard Strip ──

function KpiCell({
  label,
  mainValue,
  pnl,
  details,
}: {
  label: string;
  mainValue: string | number;
  pnl?: number;
  details: string;
}) {
  return (
    <div className="flex-1 px-5 py-3 min-w-0">
      <span className="text-[11px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">{label}</span>
      <div className="flex items-baseline gap-2.5 mt-1">
        <span className="text-2xl font-bold tabular-nums tracking-tight leading-none">{mainValue}</span>
        {pnl !== undefined && pnl !== null && (
          <span className="inline-flex items-center gap-0.5 text-sm tabular-nums font-semibold"
            style={{ color: pnl >= 0 ? "var(--color-green)" : "var(--color-red)" }}>
            {pnl >= 0 ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
            {formatPnl(pnl)}
          </span>
        )}
      </div>
      <div className="text-xs text-[var(--color-text-muted)] mt-1 truncate">{details}</div>
    </div>
  );
}

function DashboardStrip({
  totalUsd,
  totalTokens,
  connectorCount,
  portfolioPnl,
  botCount,
  controllerCount,
  botPnl,
  botVolume,
  activeExecutorCount,
  allExecutors,
  agents,
  period,
  onPeriodChange,
  onNavigate,
}: {
  totalUsd: number;
  totalTokens: number;
  connectorCount: number;
  portfolioPnl: number | null;
  botCount: number;
  controllerCount: number;
  botPnl: number;
  botVolume: number;
  activeExecutorCount: number;
  allExecutors: import("@/lib/api").ExecutorInfo[];
  agents: AgentSummary[];
  period: string;
  onPeriodChange: (p: string) => void;
  onNavigate: (path: string) => void;
}) {
  const execStats = useMemo(() => computeExecutorStats(allExecutors, period), [allExecutors, period]);

  const activeAgents = agents.filter((a) => a.status === "running" || a.status === "active");
  const agentPnl = agents.reduce((s, a) => s + a.daily_pnl, 0);
  const agentSessions = agents.reduce((s, a) => s + a.session_count, 0);

  const btnClass = "flex items-center justify-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] w-full";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      <div className="flex items-stretch divide-x divide-[var(--color-border)]">
        <KpiCell
          label="Portfolio"
          mainValue={formatUsd(totalUsd)}
          pnl={portfolioPnl ?? undefined}
          details={`${totalTokens} assets · ${connectorCount} connector${connectorCount !== 1 ? "s" : ""}`}
        />

        <KpiCell
          label="Bots"
          mainValue={botCount}
          pnl={botPnl}
          details={`${controllerCount} controller${controllerCount !== 1 ? "s" : ""} · vol ${formatUsd(botVolume)}`}
        />

        <KpiCell
          label="Executors"
          mainValue={`${activeExecutorCount} active`}
          pnl={execStats.pnl}
          details={`${execStats.count} in ${period} · vol ${formatUsd(execStats.volume)}`}
        />

        <KpiCell
          label="Agents"
          mainValue={activeAgents.length}
          pnl={agentPnl}
          details={`${agents.length} total · ${agentSessions} session${agentSessions !== 1 ? "s" : ""}`}
        />

        {/* ── Period + Quick Links ── */}
        <div className="flex flex-col justify-center gap-1 px-2.5 py-2 shrink-0 w-[100px]">
          <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] p-0.5 bg-[var(--color-bg)] w-full justify-center">
            {TIME_PERIODS.map((p) => (
              <button
                key={p}
                onClick={() => onPeriodChange(p)}
                className={`px-1.5 py-1 text-[10px] rounded font-medium transition-colors flex-1 ${
                  period === p
                    ? "bg-[var(--color-accent)] text-white shadow-sm"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
          <button onClick={() => onNavigate("/settings?tab=keys")} className={btnClass}>
            <KeyRound className="h-3 w-3" />
            Keys
          </button>
          <button onClick={() => onNavigate("/settings?tab=servers")} className={btnClass}>
            <Server className="h-3 w-3" />
            Servers
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Horizontal Bar Chart ──

function TokenBarChart({
  tokens,
  title,
  totalPortfolioValue,
}: {
  tokens: { token: string; usd_value: number; connector: string }[];
  title: string;
  totalPortfolioValue: number;
}) {
  if (tokens.length === 0) return null;
  const maxVal = tokens[0]?.usd_value ?? 0;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 h-full flex flex-col">
      <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">{title}</h3>
      <div className="flex flex-col gap-2.5 flex-1 justify-center">
        {tokens.map((t, i) => {
          const barPct = maxVal > 0 ? (t.usd_value / maxVal) * 100 : 0;
          const allocPct = totalPortfolioValue > 0 ? (t.usd_value / totalPortfolioValue) * 100 : 0;
          return (
            <div key={`${t.connector}-${t.token}`} className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 w-16 justify-end shrink-0">
                <div
                  className="h-2.5 w-2.5 rounded-sm shrink-0"
                  style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }}
                />
                <span className="text-xs font-medium truncate">{t.token}</span>
              </div>
              <div className="flex-1 h-5 rounded bg-[var(--color-bg)] overflow-hidden relative">
                <div
                  className="h-full rounded transition-all duration-500"
                  style={{
                    width: `${Math.max(barPct, 2)}%`,
                    backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
                    opacity: 0.8,
                  }}
                />
              </div>
              <span className="text-xs tabular-nums text-[var(--color-text-muted)] shrink-0 text-right">
                {allocPct.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Allocation Bar ──

function AllocationBar({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-[var(--color-border)]">
        <div
          className="h-full rounded-full bg-[var(--color-accent)]"
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className="w-12 text-right text-xs tabular-nums text-[var(--color-text-muted)]">
        {pct < 0.1 ? "<0.1" : pct.toFixed(1)}%
      </span>
    </div>
  );
}

// ── Table Rows ──

function TokenRow({ b, totalPortfolio }: { b: BalanceItem; totalPortfolio: number }) {
  const price = b.total > 0 ? b.usd_value / b.total : 0;
  const pct = totalPortfolio > 0 ? (b.usd_value / totalPortfolio) * 100 : 0;

  return (
    <tr className="border-b border-[var(--color-border)]/30 last:border-0 hover:bg-[var(--color-surface-hover)]/50">
      <td className="py-2 pl-11 pr-4">
        <span className="text-sm font-medium">{b.token}</span>
      </td>
      <td className="px-4 py-2 text-right text-sm tabular-nums">{formatAmount(b.total)}</td>
      <td className="px-4 py-2 text-right text-sm tabular-nums text-[var(--color-text-muted)]">
        {formatPrice(price)}
      </td>
      <td className="px-4 py-2 text-right text-sm tabular-nums font-medium">
        {formatUsd(b.usd_value)}
      </td>
      <td className="px-4 py-2">
        <AllocationBar pct={pct} />
      </td>
    </tr>
  );
}

function ConnectorRow({
  connector,
  totalPortfolio,
}: {
  connector: ConnectorBalance;
  totalPortfolio: number;
}) {
  const [expanded, setExpanded] = useState(true);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const pct = totalPortfolio > 0 ? (connector.total_usd / totalPortfolio) * 100 : 0;

  return (
    <>
      <tr
        className="cursor-pointer border-b border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <Chevron className="h-4 w-4 text-[var(--color-text-muted)]" />
            <span className="font-semibold">{connector.connector}</span>
            <span className="text-xs text-[var(--color-text-muted)]">
              {connector.balances.length} token{connector.balances.length !== 1 ? "s" : ""}
            </span>
          </div>
        </td>
        <td className="px-4 py-3" />
        <td className="px-4 py-3" />
        <td className="px-4 py-3 text-right font-semibold tabular-nums">
          {formatUsd(connector.total_usd)}
        </td>
        <td className="px-4 py-3">
          <span className="text-sm tabular-nums text-[var(--color-text-muted)]">
            {pct.toFixed(1)}%
          </span>
        </td>
      </tr>
      {expanded &&
        connector.balances.map((b) => (
          <TokenRow key={b.token} b={b} totalPortfolio={totalPortfolio} />
        ))}
    </>
  );
}

// ── Portfolio Evolution Chart ──

function formatAxisTime(ts: number, range: string) {
  const d = new Date(ts * 1000);
  if (range === "1D") return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (range === "1W") return d.toLocaleDateString("en-US", { weekday: "short" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatTooltipDate(ts: number, range: string) {
  const d = new Date(ts * 1000);
  if (range === "1D")
    return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function PortfolioEvolution({ server, range }: { server: string; range: string }) {
  const [stacked, setStacked] = useState(false);
  const [hover, setHover] = useState<{ x: number; point: PortfolioHistoryPoint } | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!server) return;
    TIME_PERIODS.forEach((r) => {
      queryClient.prefetchQuery({
        queryKey: ["portfolio-history", server, r],
        queryFn: () => api.getPortfolioHistory(server, r),
        staleTime: 60000,
      });
    });
  }, [server, queryClient]);

  const { data, isLoading } = useQuery({
    queryKey: ["portfolio-history", server, range],
    queryFn: () => api.getPortfolioHistory(server, range),
    enabled: !!server,
    refetchInterval: 60000,
  });

  const { data: breakdownData } = useQuery({
    queryKey: ["portfolio-history-breakdown", server, range],
    queryFn: () => api.getPortfolioHistory(server, range, true),
    enabled: !!server && stacked,
    refetchInterval: 60000,
  });

  const activeData: PortfolioHistoryResponse | undefined = stacked && breakdownData ? breakdownData : data;
  const points = activeData?.points ?? [];
  const topTokens = activeData?.top_tokens ?? [];

  const W = 800;
  const H = 250;
  const PAD = { top: 20, right: 16, bottom: 30, left: 64 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  // Compute scales
  const minTs = points.length > 0 ? points[0].timestamp : 0;
  const maxTs = points.length > 0 ? points[points.length - 1].timestamp : 1;
  const values = points.map((p) => p.total_usd);
  const minVal = stacked ? 0 : (values.length > 0 ? Math.min(...values) * 0.98 : 0);
  const maxVal = values.length > 0 ? Math.max(...values) * 1.02 : 1;
  const valRange = maxVal - minVal || 1;
  const tsRange = maxTs - minTs || 1;

  const toX = (ts: number) => PAD.left + ((ts - minTs) / tsRange) * plotW;
  const toY = (val: number) => PAD.top + plotH - ((val - minVal) / valRange) * plotH;

  // Build stacked area paths
  const stackedAreas: { token: string; color: string; path: string }[] = [];
  if (stacked && topTokens.length > 0 && points.length > 1) {
    // For each point, compute cumulative bounds per token
    const tokenOrder = topTokens;
    for (let ti = 0; ti < tokenOrder.length; ti++) {
      const token = tokenOrder[ti];
      const color = CHART_COLORS[ti % CHART_COLORS.length];

      // Upper line (cumulative up to and including this token)
      const upperPoints = points.map((p) => {
        let cumUpper = 0;
        for (let j = 0; j <= ti; j++) {
          cumUpper += p.tokens?.[tokenOrder[j]] ?? 0;
        }
        return { x: toX(p.timestamp).toFixed(2), y: toY(cumUpper).toFixed(2) };
      });

      // Lower line (cumulative up to but NOT including this token)
      const lowerPoints = points.map((p) => {
        let cumLower = 0;
        for (let j = 0; j < ti; j++) {
          cumLower += p.tokens?.[tokenOrder[j]] ?? 0;
        }
        return { x: toX(p.timestamp).toFixed(2), y: toY(cumLower).toFixed(2) };
      });

      const upperPath = upperPoints.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`).join(" ");
      const lowerPath = [...lowerPoints].reverse().map((pt, i) => `${i === 0 ? "L" : "L"} ${pt.x} ${pt.y}`).join(" ");
      const areaPath = `${upperPath} ${lowerPath} Z`;

      stackedAreas.push({ token, color, path: areaPath });
    }
  }

  // Build line path (normal mode)
  const linePath =
    !stacked && points.length > 1
      ? points.map((p, i) => `${i === 0 ? "M" : "L"} ${toX(p.timestamp).toFixed(2)} ${toY(p.total_usd).toFixed(2)}`).join(" ")
      : "";

  const areaPath =
    !stacked && points.length > 1
      ? linePath +
        ` L ${toX(points[points.length - 1].timestamp).toFixed(2)} ${(PAD.top + plotH).toFixed(2)}` +
        ` L ${toX(points[0].timestamp).toFixed(2)} ${(PAD.top + plotH).toFixed(2)} Z`
      : "";

  // Y-axis gridlines (5 lines)
  const yTicks = Array.from({ length: 5 }, (_, i) => minVal + (valRange * i) / 4);

  // X-axis labels (5 labels)
  const xTicks = Array.from({ length: 5 }, (_, i) => minTs + (tsRange * i) / 4);

  // Handle mouse move
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (points.length === 0) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * W;
    const ts = minTs + ((mouseX - PAD.left) / plotW) * tsRange;

    let closest = points[0];
    let closestDist = Math.abs(points[0].timestamp - ts);
    for (const p of points) {
      const dist = Math.abs(p.timestamp - ts);
      if (dist < closestDist) {
        closest = p;
        closestDist = dist;
      }
    }
    setHover({ x: toX(closest.timestamp), point: closest });
  };

  // Tooltip dimensions for stacked mode
  const hoverTokens = hover?.point.tokens ?? {};
  const hoverTokenEntries = stacked && topTokens.length > 0
    ? topTokens.filter((t) => (hoverTokens[t] ?? 0) > 0).map((t) => ({ token: t, value: hoverTokens[t] ?? 0, color: CHART_COLORS[topTokens.indexOf(t) % CHART_COLORS.length] }))
    : [];
  const tooltipH = stacked && hoverTokenEntries.length > 0 ? 28 + hoverTokenEntries.length * 16 : 40;
  const tooltipW = stacked && hoverTokenEntries.length > 0 ? 150 : 108;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 h-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-[var(--color-text-muted)]">Portfolio Evolution</h3>
        <div className="flex gap-0.5 rounded border border-[var(--color-border)] p-0.5">
          <button
            onClick={() => setStacked(false)}
            className={`p-1 rounded transition-colors ${!stacked ? "bg-[var(--color-accent)] text-white" : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"}`}
            title="Line chart"
          >
            <BarChart3 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setStacked(true)}
            className={`p-1 rounded transition-colors ${stacked ? "bg-[var(--color-accent)] text-white" : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"}`}
            title="Stacked area chart"
          >
            <Layers className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-[250px] text-[var(--color-text-muted)] text-sm">
          Loading...
        </div>
      ) : points.length === 0 ? (
        <div className="flex items-center justify-center h-[250px] text-[var(--color-text-muted)] text-sm">
          No history data available
        </div>
      ) : (
        <>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            width="100%"
            className="overflow-visible"
            onMouseMove={handleMouseMove}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.3" />
                <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0.02" />
              </linearGradient>
            </defs>

            {/* Y gridlines + labels */}
            {yTicks.map((val, i) => (
              <g key={i}>
                <line
                  x1={PAD.left}
                  x2={W - PAD.right}
                  y1={toY(val)}
                  y2={toY(val)}
                  stroke="var(--color-border)"
                  strokeDasharray="4 4"
                  strokeWidth="0.5"
                />
                <text
                  x={PAD.left - 8}
                  y={toY(val) + 4}
                  textAnchor="end"
                  fill="var(--color-text-muted)"
                  fontSize="10"
                >
                  {formatUsd(val)}
                </text>
              </g>
            ))}

            {/* X labels */}
            {xTicks.map((ts, i) => (
              <text
                key={i}
                x={toX(ts)}
                y={H - 4}
                textAnchor="middle"
                fill="var(--color-text-muted)"
                fontSize="10"
              >
                {formatAxisTime(ts, range)}
              </text>
            ))}

            {stacked ? (
              <>
                {/* Stacked area fills (render bottom-to-top, so reverse for correct layering) */}
                {[...stackedAreas].reverse().map((area) => (
                  <path key={area.token} d={area.path} fill={area.color} opacity="0.7" />
                ))}
              </>
            ) : (
              <>
                {/* Area fill */}
                <path d={areaPath} fill="url(#areaGrad)" />
                {/* Line */}
                <path d={linePath} fill="none" stroke="var(--color-accent)" strokeWidth="2" />
              </>
            )}

            {/* Hover elements */}
            {hover && (
              <>
                <line
                  x1={hover.x}
                  x2={hover.x}
                  y1={PAD.top}
                  y2={PAD.top + plotH}
                  stroke="var(--color-text-muted)"
                  strokeDasharray="4 4"
                  strokeWidth="1"
                />
                {!stacked && (
                  <circle
                    cx={hover.x}
                    cy={toY(hover.point.total_usd)}
                    r="4"
                    fill="var(--color-accent)"
                    stroke="var(--color-surface)"
                    strokeWidth="2"
                  />
                )}
                {/* Tooltip */}
                <g transform={`translate(${hover.x < W / 2 ? hover.x + 12 : hover.x - tooltipW - 12}, ${PAD.top + 8})`}>
                  <rect
                    width={tooltipW}
                    height={tooltipH}
                    rx="4"
                    fill="var(--color-surface)"
                    stroke="var(--color-border)"
                  />
                  <text x="8" y="16" fill="var(--color-text-muted)" fontSize="10">
                    {formatTooltipDate(hover.point.timestamp, range)}
                  </text>
                  {stacked && hoverTokenEntries.length > 0 ? (
                    <>
                      {hoverTokenEntries.map((entry, i) => (
                        <g key={entry.token} transform={`translate(0, ${24 + i * 16})`}>
                          <rect x="8" y="0" width="6" height="6" rx="1" fill={entry.color} />
                          <text x="18" y="7" fill="var(--color-text)" fontSize="9">
                            {entry.token}
                          </text>
                          <text x={tooltipW - 8} y="7" fill="var(--color-text-muted)" fontSize="9" textAnchor="end">
                            {formatUsd(entry.value)}
                          </text>
                        </g>
                      ))}
                    </>
                  ) : (
                    <text x="8" y="32" fill="var(--color-text)" fontSize="12" fontWeight="bold">
                      {formatUsd(hover.point.total_usd)}
                    </text>
                  )}
                </g>
              </>
            )}

            {/* Invisible overlay for mouse events */}
            <rect
              x={PAD.left}
              y={PAD.top}
              width={plotW}
              height={plotH}
              fill="transparent"
            />
          </svg>

          {/* Legend for stacked mode */}
          {stacked && topTokens.length > 0 && (
            <div className="flex flex-wrap gap-3 mt-2 px-1">
              {topTokens.map((token, i) => (
                <div key={token} className="flex items-center gap-1.5">
                  <div
                    className="h-2.5 w-2.5 rounded-sm"
                    style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }}
                  />
                  <span className="text-xs text-[var(--color-text-muted)]">{token}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Main Portfolio Page ──

export function Portfolio() {
  const { server } = useServer();
  const navigate = useNavigate();
  const [period, setPeriod] = useState<string>("1W");

  const { data, isLoading, error, isPlaceholderData } = useQuery({
    queryKey: ["portfolio", server],
    queryFn: () => api.getPortfolio(server!),
    enabled: !!server,
    refetchInterval: 15000,
    placeholderData: keepPreviousData,
  });

  const { data: bots } = useQuery({
    queryKey: ["bots", server],
    queryFn: () => api.getBots(server!),
    enabled: !!server,
    refetchInterval: 30000,
    placeholderData: keepPreviousData,
  });

  const { data: allExecutors } = useQuery({
    queryKey: ["executors-all", server],
    queryFn: () => api.getExecutors(server!),
    enabled: !!server,
    refetchInterval: 30000,
    placeholderData: keepPreviousData,
  });

  const { data: agents } = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.getAgents(),
    refetchInterval: 30000,
    placeholderData: keepPreviousData,
  });

  const { data: periodHistory } = useQuery({
    queryKey: ["portfolio-history", server, period],
    queryFn: () => api.getPortfolioHistory(server!, period),
    enabled: !!server,
    refetchInterval: 60000,
  });

  if (!server) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center max-w-sm">
          <Server className="h-10 w-10 mx-auto mb-3 text-[var(--color-text-muted)]" />
          <h2 className="text-lg font-semibold mb-1">No Server Selected</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            Select a server from the sidebar to view your portfolio.
          </p>
        </div>
      </div>
    );
  }

  if (isLoading && !data) {
    return (
      <div className="space-y-6">
        {/* Skeleton dashboard strip */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-4 flex gap-8">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex-1">
              <div className="h-3 w-16 rounded bg-[var(--color-border)] animate-pulse mb-2" />
              <div className="h-7 w-24 rounded bg-[var(--color-border)] animate-pulse mb-1.5" />
              <div className="h-3 w-32 rounded bg-[var(--color-border)] animate-pulse" />
            </div>
          ))}
        </div>
        {/* Skeleton chart */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <div className="h-[250px] rounded bg-[var(--color-border)]/30 animate-pulse" />
        </div>
        {/* Skeleton table */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-4 rounded bg-[var(--color-border)] animate-pulse" style={{ width: `${85 - i * 10}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center max-w-sm">
          <AlertCircle className="h-10 w-10 mx-auto mb-3 text-[var(--color-red)]" />
          <h2 className="text-lg font-semibold mb-1">Failed to Load Portfolio</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
        </div>
      </div>
    );
  }

  const totalUsd = data?.total_usd ?? 0;
  const connectors = data?.connectors ?? [];

  // Compute portfolio PnL from history (follows period selector)
  const historyPoints = periodHistory?.points ?? [];
  const portfolioPnl =
    historyPoints.length >= 2
      ? historyPoints[historyPoints.length - 1].total_usd - historyPoints[0].total_usd
      : null;

  // Compute aggregate stats
  const totalTokens = connectors.reduce((s, c) => s + c.balances.length, 0);
  const botsList = bots?.bots ?? [];
  const controllerCount = bots?.controllers?.length ?? 0;
  const botPnl = bots?.total_pnl ?? 0;
  const botVolume = bots?.total_volume ?? 0;
  const activeExecutorCount = (allExecutors ?? []).filter((e) => isExecutorActive(e.status)).length;

  // Flatten all tokens for top holdings
  const allTokens = connectors.flatMap((c) =>
    c.balances.map((b) => ({ token: b.token, usd_value: b.usd_value, connector: c.connector })),
  );
  allTokens.sort((a, b) => b.usd_value - a.usd_value);
  const topTokens = allTokens.slice(0, 8);

  return (
    <div className={`space-y-6 transition-opacity duration-300 ${isPlaceholderData ? "opacity-60" : "opacity-100"}`}>
      {/* Dashboard Strip */}
      <DashboardStrip
        totalUsd={totalUsd}
        totalTokens={totalTokens}
        connectorCount={connectors.length}
        portfolioPnl={portfolioPnl}
        botCount={botsList.length}
        controllerCount={controllerCount}
        botPnl={botPnl}
        botVolume={botVolume}
        activeExecutorCount={activeExecutorCount}
        allExecutors={allExecutors ?? []}
        agents={agents ?? []}
        period={period}
        onPeriodChange={setPeriod}
        onNavigate={navigate}
      />

      {/* Portfolio Evolution + Top Holdings side by side */}
      <div className="flex flex-col lg:flex-row gap-4">
        <div className={connectors.length > 0 && topTokens.length > 0 ? "lg:flex-[2] min-w-0" : "w-full"}>
          <PortfolioEvolution server={server!} range={period} />
        </div>
        {connectors.length > 0 && topTokens.length > 0 && (
          <div className="lg:flex-1 min-w-0">
            <TokenBarChart tokens={topTokens} title="Top Holdings" totalPortfolioValue={totalUsd} />
          </div>
        )}
      </div>

      {/* Balance table */}
      {connectors.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
          <Wallet className="h-10 w-10" />
          <p>No balances found</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Token
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Amount
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Price
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Value
                </th>
                <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Allocation
                </th>
              </tr>
            </thead>
            <tbody>
              {connectors.map((c) => (
                <ConnectorRow key={c.connector} connector={c} totalPortfolio={totalUsd} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

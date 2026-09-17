import { useQuery } from "@tanstack/react-query";
import { Zap } from "lucide-react";
import { memo, useMemo } from "react";

import { AgentPnlChart, sessionsToDataPoints } from "@/components/agent/AgentPnlChart";
import { api, type AgentPerformance } from "@/lib/api";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume, pnlTextClass } from "@/lib/formatters";

const EMPTY_SESSIONS: AgentPerformance[] = [];

/**
 * Memoized because its only host, `StrategyWorkbench`, re-renders on every
 * `executors:<server>` WS frame (~2 s) while a loop runs, and its props are
 * primitives; its own query still re-renders it when the rollup changes
 * (PERF-385).
 */
export const PerformancePanel = memo(function PerformancePanel({
  slug,
  sslug,
  dense = false,
}: {
  slug: string;
  sslug: string;
  /**
   * Half a workspace row rather than a page. A prop, not a media query: the
   * window is wide in both cases, and `lg:grid-cols-8` in a 640px column is
   * eight stat tiles four characters wide.
   */
  dense?: boolean;
}) {
  const { data } = useQuery({
    queryKey: ["strategy-performance", slug, sslug],
    queryFn: () => api.getStrategyPerformance(slug, sslug),
    refetchInterval: 10000,
  });
  const totals = data?.totals || {};
  // A dry run books nothing by construction — that is what makes it a dry run —
  // so it is never folded into the totals below, which are about money that
  // moved. The runs themselves are listed in the Lab (FEAT-099); what stays
  // here is the strategy-level view: the KPI strip and the equity curve.
  // Memoized on the query's payload (structurally shared across refetches), so
  // `pnlData` below keeps its identity between renders and the chart's
  // `setData` runs only when the sessions actually change (PERF-385).
  const allSessions = data?.sessions ?? EMPTY_SESSIONS;
  const sessions = useMemo(() => allSessions.filter((s) => s.kind === "session"), [allSessions]);
  const totalPnl = Number(totals.total_pnl ?? 0);
  const realized = Number(totals.realized_pnl ?? 0);
  const unrealized = Number(totals.unrealized_pnl ?? 0);
  const volume = Number(totals.volume ?? 0);
  const fees = Number(totals.fees ?? 0);
  const openPos = Number(totals.open_positions ?? 0);
  const pnlClass = pnlTextClass(totalPnl);

  // Only sessions whose closes carry an outcome can be averaged. A bot-mode
  // session reports its closes with win_rate === null (the controller snapshot
  // says how many positions closed, not how they ended); counting those closes
  // in the denominator would read every one of them as a loss.
  const rated = sessions.filter((x) => x.win_rate != null);
  const closed = rated.reduce((s, x) => s + x.closed_count, 0);
  const wins = rated.reduce((s, x) => s + Math.round((x.win_rate as number) * x.closed_count), 0);
  const winRate = closed > 0 ? (wins / closed) * 100 : null;
  const trades = sessions.reduce((s, x) => s + x.trade_count, 0);

  // A backend that reports no cumulative fee column leaves bot-mode fees
  // derivable only from open positions, so a flat bot sums to $0.00 — which is
  // "not reported", not "traded for free". One unknown makes the total a floor.
  const feesKnown = sessions.every((x) => x.fees_known !== false);

  // PnL chart data from session-level performance
  const pnlData = useMemo(() => sessionsToDataPoints(sessions), [sessions]);

  return (
    <div className={`space-y-4 ${dense ? "" : "lg:col-span-2"}`}>
      {/* Stat grid */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Zap className="h-3.5 w-3.5" /> Performance
        </h3>
        <div className={`grid gap-4 ${dense ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8"}`}>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-lg font-mono font-semibold ${pnlClass}`}>
              {formatCurrencyPnl(totalPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Realized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{formatCurrencyPnl(realized)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Unrealized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{formatCurrencyPnl(unrealized)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              {formatCurrencyVolume(volume)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Fees</span>
            <span className="text-lg font-mono text-[var(--color-text)]" title={feesKnown ? undefined : "Not reported by this backend"}>
              {feesKnown ? formatCurrency(fees) : "—"}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Win Rate</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              {winRate === null ? "—" : `${winRate.toFixed(0)}%`}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Trades</span>
            {/* Only round-trip closes count, which reads a directional
                controller's risk stop as churn. Rendering "0" beside real volume
                asserts something the volume contradicts — say "unknown" instead
                and let the session view show the close-type breakdown. */}
            <span className="text-lg font-mono text-[var(--color-text)]" title={trades === 0 && volume > 0 ? "No round-trip closes recorded — open the session for the close-type breakdown" : undefined}>
              {trades === 0 && volume > 0 ? "—" : trades}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{openPos}</span>
          </div>
        </div>
      </div>

      {/* PnL equity curve */}
      {pnlData.length > 1 && (
        <AgentPnlChart data={pnlData} height={180} title="PnL Equity Curve" />
      )}

    </div>
  );
});

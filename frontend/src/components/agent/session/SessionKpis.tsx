import { AlertTriangle, FileText } from "lucide-react";

import { closeSummary } from "@/components/agent/session/closeTypes";
import type { AgentPerformance } from "@/lib/api";
import { formatCompactUsd, formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

// ── Session KPIs ──
//
// Driven by `performance`, never by the executor rows. A session trading through
// bots has no rows to derive anything from — its executors live inside the bot
// instance's own database — so deriving the strip from rows made a session that
// traded $1.2k and lost $1.46 render as one that did nothing at all.

function Kpi({ label, value, sub, className = "" }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div>
      <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
      <span className={`font-mono text-sm font-semibold ${className || "text-[var(--color-text)]"}`}>{value}</span>
      {sub && <span className="block text-[9px] text-[var(--color-text-muted)]/70">{sub}</span>}
    </div>
  );
}

export function SessionKpis({
  perf,
  summary,
  hasReport,
  onOpenReport,
}: {
  perf?: AgentPerformance | null;
  /**
   * The run's own headline facts — its status and how far it has ticked.
   *
   * It carried the last action too, truncated to one line, until the answer
   * stack put that sentence *whole* six pixels below this strip (FEAT-119).
   * Optional rather than removed from `ParsedJournal`'s summary, so a caller
   * can go on handing this the summary it already has.
   */
  summary?: { status: string; lastTick: number; lastAction?: string };
  hasReport?: boolean;
  onOpenReport?: () => void;
}) {
  const total = perf?.total_pnl ?? 0;
  const closes = closeSummary(perf);
  const trades = perf?.trade_count ?? 0;
  const volume = perf?.volume ?? 0;
  const feesKnown = perf?.fees_known !== false;

  // A round-trip count of zero next to real volume is the strict close-type
  // filter refusing to read a directional controller's risk stop as a trade.
  // Show the closes that did happen rather than a bare "0" the numbers contradict.
  const tradeValue =
    trades > 0 ? String(trades) : closes.total > 0 ? String(closes.total) : volume > 0 ? "—" : "0";
  const tradeSub = trades === 0 && closes.total > 0 ? closes.label : undefined;

  const status = summary?.status || "";
  const statusClass =
    status === "ACTIVE" || status === "running"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
      : status === "paused"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
        : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]";

  return (
    <div className="space-y-2">
      {(perf?.unresolved_bases?.length ?? 0) > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-2.5 text-xs text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            <strong>Incomplete.</strong> No live or archived instance for{" "}
            <span className="font-mono">{perf?.unresolved_bases?.join(", ")}</span>. The figures below are a
            floor — whatever those bots did is not in them.
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        {summary && (
          <div>
            <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${statusClass}`}>
              {status || "idle"}
            </span>
          </div>
        )}
        <Kpi
          label="Total PnL"
          value={formatCurrencyPnl(total)}
          className={pnlTextClass(total)}
        />
        <Kpi label="Realized" value={formatCurrencyPnl(perf?.realized_pnl ?? 0)} />
        <Kpi label="Unrealized" value={formatCurrencyPnl(perf?.unrealized_pnl ?? 0)} />
        <Kpi label="Volume" value={formatCompactUsd(volume)} />
        <Kpi
          label="Fees"
          value={feesKnown ? formatCompactUsd(perf?.fees ?? 0) : "—"}
          sub={feesKnown ? undefined : "not reported"}
        />
        <Kpi label="Closes" value={tradeValue} sub={tradeSub} />
        <Kpi label="Open" value={String(perf?.open_count ?? 0)} />
        {summary && summary.lastTick > 0 && <Kpi label="Ticks" value={`#${summary.lastTick}`} />}
        {/* The session's own live report. It has always existed — rebuilt every
            tick under a stable id — but was only reachable through the routines
            report grid, where it appeared as a routine nobody had created. */}
        {hasReport && (
          <button
            onClick={onOpenReport}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          >
            <FileText className="h-3 w-3" /> Session report
          </button>
        )}
      </div>
    </div>
  );
}

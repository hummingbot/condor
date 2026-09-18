import { AlertTriangle } from "lucide-react";

import { closeSummary } from "@/components/agent/session/closeTypes";
import type { AgentPerformance } from "@/lib/api";
import { formatCompactUsd, formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

// ── Session KPIs ──
//
// Driven by `performance`, never by the executor rows. A session trading through
// bots has no rows to derive anything from — its executors live inside the bot
// instance's own database — so deriving the strip from rows made a session that
// traded $1.2k and lost $1.46 render as one that did nothing at all.
//
// A bare row inside the Now card (ARCH-426): the card draws the border and
// holds the Session report door in its header. The run's status and tick count
// are not repeated here — the loop bar and the tick spine above already say them.

function Kpi({ label, value, sub, className = "" }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    // A long sub-label (a close breakdown like `position hold ×454, early stop
    // ×44806`) is clamped to one line and whole in the tooltip, so it cannot
    // widen its cell and wrap the row.
    <div className="min-w-0" title={sub}>
      <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
      <span className={`font-mono text-sm font-semibold ${className || "text-[var(--color-text)]"}`}>{value}</span>
      {sub && <span className="block max-w-[9rem] truncate text-[9px] text-[var(--color-text-muted)]/70">{sub}</span>}
    </div>
  );
}

export function SessionKpis({ perf }: { perf?: AgentPerformance | null }) {
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

      <div data-session-kpis className="flex flex-wrap items-start gap-x-5 gap-y-3">
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
      </div>
    </div>
  );
}

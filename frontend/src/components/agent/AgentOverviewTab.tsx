import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Zap } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AgentPnlChart, sessionsToDataPoints } from "@/components/agent/AgentPnlChart";
import { ModeBadge } from "@/components/agent/ModeBadge";
import { api } from "@/lib/api";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume, pnlTextClass } from "@/lib/formatters";

// ── Markdown Editor ──

export function MarkdownEditor({
  label,
  sublabel,
  content,
  onSave,
  invalidateKey,
  onDirtyChange,
  minHeightClass = "min-h-[500px]",
  showLabel = true,
}: {
  label: string;
  sublabel: string;
  content: string;
  onSave: (value: string) => Promise<unknown>;
  invalidateKey: unknown[];
  /** Notifies the host (e.g. a closable modal) when there are unsaved edits. */
  onDirtyChange?: (dirty: boolean) => void;
  /**
   * How tall the box starts. A near-full-screen modal can afford 500px; a card
   * sharing a row with another card inside a disclosure cannot, and a fixed
   * height there is what turns two documents into a page of scrollbars.
   */
  minHeightClass?: string;
  /**
   * Whether the box names itself. Off for a host that already has a titled
   * header of its own — a card whose chrome says "Playbook · strategy.md"
   * printing it again a row below is the same words twice in two sizes.
   */
  showLabel?: boolean;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => onSave(value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invalidateKey });
      setDirty(false);
      onDirtyChange?.(false);
    },
  });

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    setDirty(true);
    onDirtyChange?.(true);
  }, [onDirtyChange]);

  return (
    <div className="flex flex-col gap-2">
      <div className={`flex items-center ${showLabel ? "justify-between" : "justify-end"}`}>
        {showLabel ? (
          <div>
            <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{label}</span>
            <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">{sublabel}</span>
          </div>
        ) : (
          <span className="sr-only">{label}</span>
        )}
        <button
          onClick={() => saveMut.mutate()}
          disabled={!dirty || saveMut.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          <Save className="h-3.5 w-3.5" />
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {saveMut.isError && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {saveMut.error instanceof Error ? saveMut.error.message : "Save failed"}
        </div>
      )}
      <textarea
        value={value}
        onChange={handleChange}
        spellCheck={false}
        className={`${minHeightClass} w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50`}
      />
    </div>
  );
}

// ── Instance Card ──

export function InstanceCard({ instance }: { instance: import("@/lib/api").RunningInstance }) {
  const riskLimits = (instance.risk_limits || {}) as Record<string, unknown>;
  const statusColor = instance.status === "running" ? "text-emerald-400" : instance.status === "paused" ? "text-amber-400" : "text-[var(--color-text-muted)]";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold text-[var(--color-text)]">{instance.agent_id}</span>
          <span className={`text-xs font-semibold uppercase ${statusColor}`}>{instance.status}</span>
          <ModeBadge mode={instance.execution_mode} />
        </div>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
          <span>Ticks: {instance.tick_count}</span>
          <span className={pnlTextClass(instance.daily_pnl)}>
            PnL: {formatCurrencyPnl(instance.daily_pnl)}
          </span>
        </div>
      </div>

      {instance.trading_context && (
        <p className="mb-3 whitespace-pre-wrap rounded-md bg-[var(--color-surface)] p-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
          {instance.trading_context}
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-4">
        {instance.agent_key && (
          <div className="flex justify-between">
            <span className="text-[var(--color-text-muted)]">model</span>
            <span className="text-[var(--color-primary)]">{instance.agent_key}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">server</span>
          <span className="text-[var(--color-text)]">{instance.server_name || "auto"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">budget</span>
          <span className="text-[var(--color-text)]">${instance.total_amount_quote}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">frequency</span>
          <span className="text-[var(--color-text)]">{instance.frequency_sec}s</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">tick timeout</span>
          <span className="text-[var(--color-text)]">{instance.tick_timeout_sec}s</span>
        </div>
        {Object.entries(riskLimits).map(([k, v]) => {
          // These are risk LIMITS (max_*), not current values — keep the "max"
          // so e.g. "open executors: 10" isn't misread as 10 executors open now.
          const label =
            k === "max_position_size_quote"
              ? "max position"
              : k === "max_open_executors"
                ? "max executors"
                : k.replace(/_/g, " ");
          const val = k === "max_position_size_quote" ? `$${v}` : String(v);
          return (
            <div key={k} className="flex justify-between">
              <span className="text-[var(--color-text-muted)]">{label}</span>
              <span className="text-[var(--color-text)]">{val}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Performance Panel ──

export function PerformancePanel({
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
  const sessions = (data?.sessions || []).filter((s) => s.kind === "session");
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
}


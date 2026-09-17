import { ModeBadge } from "@/components/agent/ModeBadge";
import type { RunningInstance } from "@/lib/api";
import { formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

export function InstanceCard({ instance }: { instance: RunningInstance }) {
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
          <span className={pnlTextClass(instance.total_pnl)}>
            PnL: {formatCurrencyPnl(instance.total_pnl)}
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

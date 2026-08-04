import { ChevronRight, Trash2, type LucideIcon } from "lucide-react";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { StatusBadge } from "@/components/agent/StatusBadge";
import type { RunningInstance } from "@/lib/api";
import { formatCurrencyPnl } from "@/lib/formatters";

// ── Entity Card ──
//
// The one summary card for anything that loops and books PnL: an Agent on the
// fleet grid, a Strategy on the agent page. Typed structurally (like
// `deriveAgentStatus`) so `AgentSummary` and `StrategySummary` both satisfy it
// without casts. ARCH-115 folded the two hand-copied versions into this one.

/** The shape both `AgentSummary` and `StrategySummary` already satisfy. */
interface EntitySummary {
  name: string;
  description?: string;
  /** Raw backend status — `deriveAgentStatus` refines it for display. */
  status: string;
  instances?: RunningInstance[];
  session_count: number;
  daily_pnl?: number;
  total_pnl?: number;
  open_positions?: number;
}

export function EntityCard({
  entity,
  icon: Icon,
  deleteLabel,
  onClick,
  onDelete,
}: {
  entity: EntitySummary;
  icon: LucideIcon;
  /** Accessible name for the delete affordance, e.g. "Delete agent". */
  deleteLabel: string;
  onClick: () => void;
  onDelete: () => void;
}) {
  const totalPnl = entity.total_pnl ?? 0;
  const totalPnlColor = totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const dayPnl = entity.daily_pnl ?? 0;
  const dayPnlColor = dayPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const status = deriveAgentStatus(entity);
  const isLive = status === "running";

  return (
    <button
      onClick={onClick}
      className={`group relative w-full rounded-lg border text-left transition-all duration-200 hover:border-[var(--color-primary)]/40 hover:shadow-lg ${
        isLive
          ? "border-emerald-500/20 bg-emerald-500/[0.03]"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      <div className="p-4">
        <div className="mb-3 flex items-start justify-between">
          <div className="flex items-center gap-2">
            <div className={`flex h-8 w-8 items-center justify-center rounded-md ${
              isLive ? "bg-emerald-500/10 text-emerald-400" : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}>
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-[var(--color-text)]">{entity.name}</h3>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={status} />
            {entity.status !== "running" && (
              <div
                aria-label={deleteLabel}
                className="opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                onClick={(e) => { e.stopPropagation(); onDelete(); }}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onDelete(); } }}
                role="button"
                tabIndex={0}
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20">
                  <Trash2 className="h-3.5 w-3.5" />
                </span>
              </div>
            )}
          </div>
        </div>

        {entity.description && (
          <p className="mb-3 text-xs text-[var(--color-text-muted)] line-clamp-2">
            {entity.description}
          </p>
        )}

        <div className="grid grid-cols-4 gap-2 border-t border-[var(--color-border)]/50 pt-3">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-sm font-mono font-semibold ${totalPnlColor}`}>
              {formatCurrencyPnl(totalPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Session</span>
            <span className={`text-sm font-mono ${dayPnlColor}`}>
              {formatCurrencyPnl(dayPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{entity.open_positions ?? 0}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Sessions</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{entity.session_count}</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end border-t border-[var(--color-border)]/30 px-4 py-2 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100">
        <span className="text-[11px]">Open</span>
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}

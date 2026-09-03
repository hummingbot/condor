import { ChevronRight, FlaskConical, Repeat, Trash2, type LucideIcon } from "lucide-react";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { StatusBadge } from "@/components/agent/StatusBadge";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type { RunningInstance } from "@/lib/api";
import { formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

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
  /**
   * Dry runs and single ticks — every `experiment_N.md` on disk.
   *
   * The payload has carried this from the start and the card dropped it, so an
   * agent whose only history was a dry run read as an agent that had never run
   * at all. A dry run books no PnL by definition, which is exactly why the
   * count has to be said: it is the only trace the run leaves on a summary.
   */
  experiment_count?: number;
  tick_count?: number;
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
  const totalPnlColor = pnlTextClass(totalPnl);
  const dayPnl = entity.daily_pnl ?? 0;
  const dayPnlColor = pnlTextClass(dayPnl);
  const status = deriveAgentStatus(entity);
  const isLive = status === "running";
  // The loop the card is a summary of. A card that says "running" and nothing
  // about the beat is the thing this whole pass is about; one line is what the
  // grid has room for, and the panel behind the click has the rest.
  const live = entity.instances?.find((i) => i.status === "running") ?? null;
  const now = useSeconds(isLive);

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

        {live && <LiveLoopLine instance={live} now={now} />}

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
            <span className="text-sm font-mono text-[var(--color-text)]">
              {entity.session_count}
              {/* Dry runs ride in the same tile rather than claiming a fifth
                  column: they are the same question — how much has this run —
                  answered for the runs that were never allowed to trade. */}
              {!!entity.experiment_count && (
                <span
                  className="ml-1.5 text-xs text-amber-400"
                  title={`${entity.experiment_count} dry run${entity.experiment_count === 1 ? "" : "s"} / single ticks`}
                >
                  +{entity.experiment_count}
                  <FlaskConical className="ml-0.5 inline h-2.5 w-2.5" />
                </span>
              )}
            </span>
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

/**
 * The beat, on one line, for a card.
 *
 * The card's job is to be scanned in a grid, so this says the three things that
 * change minute to minute — which tick, when the next one is due, what the last
 * one did — and leaves the strip, the narration and the error to `LoopPulse`
 * in the panel the card opens. An overdue tick is named as overdue rather than
 * printed as a negative countdown, the same call the pulse and the fleet band
 * both make.
 */
function LiveLoopLine({
  instance,
  now,
}: {
  instance: RunningInstance;
  now: number;
}) {
  const dueIn =
    instance.last_tick_at > 0
      ? instance.last_tick_at + instance.frequency_sec - now / 1000
      : null;
  const did = instance.last_did;

  return (
    <div className="mb-3 flex flex-col gap-0.5 rounded-md border border-emerald-500/15 bg-emerald-500/[0.04] px-2 py-1.5">
      <div className="flex items-center gap-2 text-[11px] tabular-nums text-[var(--color-text-muted)]">
        <Repeat className="h-3 w-3 shrink-0 animate-pulse text-emerald-400" />
        <span className="font-mono">tick {instance.tick_count}</span>
        <span className="opacity-40">·</span>
        <span className="font-mono">every {countdown(instance.frequency_sec)}</span>
        <span className="opacity-40">·</span>
        <span className="font-mono">
          {dueIn === null
            ? "first tick pending"
            : dueIn > 0
              ? `next in ${countdown(dueIn)}`
              : `overdue ${countdown(-dueIn)}`}
        </span>
      </div>
      {did && (
        <span
          className={`truncate text-[11px] ${did.ok ? "text-[var(--color-text-muted)]" : "text-amber-500"}`}
          title={did.summary}
        >
          #{did.tick} {did.summary}
          {!did.ok && " — failed"}
        </span>
      )}
    </div>
  );
}

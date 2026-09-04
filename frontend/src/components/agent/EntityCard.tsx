import { ChevronRight, FlaskConical, Trash2, type LucideIcon } from "lucide-react";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { StatusBadge } from "@/components/agent/StatusBadge";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type { RunningInstance } from "@/lib/api";
import { formatAge } from "@/lib/formatters";

// ── Entity Card ──
//
// The one summary card for anything that loops: an Agent on the fleet grid, a
// Strategy on the agent page. Typed structurally (like `deriveAgentStatus`) so
// `AgentSummary` and `StrategySummary` both satisfy it without casts. ARCH-115
// folded the two hand-copied versions into this one.
//
// It used to answer with money — Total PnL, Last Session, Open — and for most
// loops that is three tiles of `+$0.00`, because most runs never traded and the
// pipeline answers "not priced" with a zero. So the tiles say what a card is
// actually asked about a loop instead (FEAT-099): when it last ticked, how many
// ticks it has run, and when the next one is due. Money is a strategy-level
// question and lives where it can be read properly — the Lab's KPI strip and
// the workbench's performance panel.

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
  const status = deriveAgentStatus(entity);
  const isLive = status === "running";
  // The loop the card is a summary of. A card that says "running" and nothing
  // about the beat is the thing this whole pass is about.
  const live = entity.instances?.find((i) => i.status === "running") ?? null;
  const now = useSeconds(isLive);

  const ticks = live?.tick_count ?? entity.tick_count ?? 0;
  const lastTickAt = live?.last_tick_at ?? 0;
  const dueIn =
    live && lastTickAt > 0 ? lastTickAt + live.frequency_sec - now / 1000 : null;

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

        {live?.last_did && <LastDeedLine did={live.last_did} />}

        <div className="grid grid-cols-4 gap-2 border-t border-[var(--color-border)]/50 pt-3">
          <Fact
            label="Last tick"
            value={lastTickAt > 0 ? `${formatAge(lastTickAt)} ago` : "\u2014"}
          />
          <Fact label="Ticks" value={String(ticks)} />
          <Fact
            label="Next tick"
            // An overdue tick is named as overdue rather than printed as a
            // negative countdown — the same call the pulse and the fleet band
            // both make.
            value={
              dueIn === null
                ? "\u2014"
                : dueIn > 0
                  ? countdown(dueIn)
                  : `overdue ${countdown(-dueIn)}`
            }
            sub={live ? `every ${countdown(live.frequency_sec)}` : undefined}
          />
          <Fact
            label="Runs"
            value={String(entity.session_count)}
            chip={
              entity.experiment_count ? (
                <span
                  className="flex items-center gap-0.5 rounded bg-amber-500/10 px-1 py-0.5 text-[9px] font-bold uppercase text-amber-400"
                  title={`${entity.experiment_count} dry run${entity.experiment_count === 1 ? "" : "s"} / single ticks`}
                >
                  <FlaskConical className="h-2.5 w-2.5" />
                  {entity.experiment_count} dry
                </span>
              ) : undefined
            }
          />
        </div>
      </div>

      <div className="flex items-center justify-end border-t border-[var(--color-border)]/30 px-4 py-2 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100">
        <span className="text-[11px]">Open</span>
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}

/** One tile of the card's fact row. */
function Fact({
  label,
  value,
  sub,
  chip,
}: {
  label: string;
  value: string;
  sub?: string;
  chip?: React.ReactNode;
}) {
  return (
    <div>
      <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className="flex items-center gap-1 text-sm font-mono text-[var(--color-text)]">
        {value}
        {chip}
      </span>
      {sub && (
        <span className="block text-[9px] text-[var(--color-text-muted)]/70">{sub}</span>
      )}
    </div>
  );
}

/**
 * What the last tick actually **did**, on one line.
 *
 * The card's job is to be scanned in a grid, so the cadence facts are tiles and
 * this is the one thing a tile cannot hold: a sentence. It is the deed — a tool
 * call that ran — and not the model's narration of it; `LoopPulse` in the panel
 * behind the click shows both, plus the strip and the error.
 */
function LastDeedLine({ did }: { did: NonNullable<RunningInstance["last_did"]> }) {
  return (
    <div className="mb-3 rounded-md border border-emerald-500/15 bg-emerald-500/[0.04] px-2 py-1.5">
      <span
        className={`block truncate text-[11px] ${did.ok ? "text-[var(--color-text-muted)]" : "text-amber-500"}`}
        title={did.summary}
      >
        #{did.tick} {did.summary}
        {!did.ok && " \u2014 failed"}
      </span>
    </div>
  );
}

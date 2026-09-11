import { Brain, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Clock, Search, Square, X, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import type { RoutineInfo, RoutineInstance } from "@/lib/api";
import { formatRelativeTime, toMs } from "@/lib/formatters";
import { formatInterval, formatSlug, routineAgent, routineAgents } from "@/lib/routineUtils";

/** What the latest report of a routine says about it, for the card's footer. */
export interface RoutineReportInfo {
  title: string;
  created_at: string;
  count: number;
  tags: string[];
}

/**
 * The library's left column: whose routines, which one, and what has been run.
 *
 * This is the `/routines` launcher page, folded into the browser it used to
 * open (FEAT-091). The page was a grid of cards over a strip of runs, and
 * clicking a card threw both away for a full-screen browser whose own sidebar
 * was a flat list — two renderings of one list, one of them reachable only by
 * leaving the other. The cards, the agent bubbles, the search box and the runs
 * strip live here now, beside the report they pick, so the page *is* the
 * browser and there is nothing to open.
 *
 * Collapsed it is the 48px rail the workspace pane opens on, which is the one
 * thing that fits beside a conversation.
 */
export function RoutineLibrarySidebar({
  routines,
  listed,
  instances,
  activeSource,
  onSelect,
  scope,
  onScopeChange,
  search,
  onSearchChange,
  compact,
  onToggleCompact,
  onStopInstance,
  stopping = false,
  listRef,
  reportInfo,
}: {
  /** Every routine, for the bubbles' counts — not just the ones in scope. */
  routines: RoutineInfo[];
  /** What the list renders: the picked scope, narrowed by the search box. */
  listed: RoutineInfo[];
  instances: RoutineInstance[];
  activeSource: string;
  onSelect: (name: string) => void;
  scope: string;
  onScopeChange: (next: string) => void;
  search: string;
  onSearchChange: (next: string) => void;
  compact: boolean;
  onToggleCompact: () => void;
  onStopInstance: (instanceId: string) => void;
  stopping?: boolean;
  /** The browser scrolls the active card into view through this. */
  listRef: React.RefObject<HTMLDivElement | null>;
  reportInfo: Map<string, RoutineReportInfo>;
}) {
  const agents = useMemo(() => routineAgents(routines), [routines]);
  const condorCount = routines.filter((r) => !routineAgent(r)).length;
  const agentCount = routines.length - condorCount;

  const live = (status: string) => status === "running" || status === "scheduled";

  // Every run this process still holds, newest first — not just the ones still
  // going. A one-shot that takes 30s was only ever visible for those 30s, and a
  // run that rendered no report (a failed one, most of all) would then exist
  // nowhere in the UI at all.
  const runs = useMemo(
    () =>
      [...instances].sort(
        (a, b) => toMs(b.last_run_at ?? b.created_at) - toMs(a.last_run_at ?? a.created_at),
      ),
    [instances],
  );
  const [showAllRuns, setShowAllRuns] = useState(false);
  // Anything still live is exempt from the cap: those are the only rows with a
  // Stop button, so hiding one would hide the action.
  const shownRuns = useMemo(() => {
    if (showAllRuns || runs.length <= RUNS_COLLAPSED_LIMIT) return runs;
    const keep = new Set(runs.filter((i) => live(i.status)).map((i) => i.instance_id));
    for (const i of runs) {
      if (keep.size >= RUNS_COLLAPSED_LIMIT) break;
      keep.add(i.instance_id);
    }
    return runs.filter((i) => keep.has(i.instance_id));
  }, [runs, showAllRuns]);
  const hiddenRuns = runs.length - shownRuns.length;

  /** One scope bubble. `"routine"` is the older spelling of `"condor"`. */
  const bubble = (
    value: string,
    label: string,
    count: number,
    Icon?: typeof Brain,
  ) => {
    const on = scope === value || (value === "condor" && scope === "routine");
    const agentish = value !== "all" && value !== "condor";
    return (
      <button
        key={value}
        onClick={() => onScopeChange(value)}
        aria-pressed={on}
        data-scope={value}
        title={`Show ${label.toLowerCase()}`}
        className={`flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium transition-all ${
          on
            ? agentish
              ? "bg-purple-500 text-white"
              : "bg-[var(--color-primary)] text-white"
            : agentish
              ? "bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
              : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        }`}
      >
        {Icon && <Icon className="h-2.5 w-2.5" />}
        {label}
        {count > 0 && <span className="opacity-70">{count}</span>}
      </button>
    );
  };

  return (
    <div
      className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
        compact ? "w-12" : "w-64"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
        {!compact && (
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Routines
          </span>
        )}
        <button
          onClick={onToggleCompact}
          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          title={compact ? "Expand the library" : "Collapse to the rail"}
        >
          {compact ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
        </button>
      </div>

      {!compact && (
        <>
          {/* Search */}
          <div className="relative border-b border-[var(--color-border)] px-2 py-2">
            <Search className="absolute left-4 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Search routines…"
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-1 pl-7 pr-6 text-[11px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/70 focus:border-[var(--color-primary)] focus:outline-none"
            />
            {search && (
              <button
                onClick={() => onSearchChange("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Clear search"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* Whose routines this list shows. The bubbles sit on the list they
              filter; the header's select is what the collapsed rail falls back
              to, where there is no list to put them beside. */}
          <div className="flex flex-wrap gap-1 border-b border-[var(--color-border)] px-2 py-2">
            {bubble("all", "All", routines.length)}
            {condorCount > 0 && bubble("condor", "Condor", condorCount, Zap)}
            {agentCount > 0 && bubble("agent", "Agents", agentCount, Brain)}
            {agents.map((slug) =>
              bubble(slug, formatSlug(slug), 0, Brain),
            )}
          </div>
        </>
      )}

      {/* The routines themselves */}
      <div
        ref={listRef}
        className={`flex-1 overflow-y-auto scrollbar-thin ${compact ? "" : "space-y-1.5 p-2"}`}
      >
        {listed.length === 0 && !compact && (
          <p className="px-1 py-6 text-center text-[11px] text-[var(--color-text-muted)]">
            {routines.length === 0 ? "No routines available" : "No matches"}
          </p>
        )}
        {listed.map((r) => {
          const isActive = r.name === activeSource;
          const hasActiveInstance = instances.some(
            (i) => i.routine_name === r.name && live(i.status),
          );
          const owner = routineAgent(r);
          const displayName = r.name.replace(/_/g, " ");
          const info = reportInfo.get(r.name);
          const reportCount = info?.count ?? r.report_count;

          if (compact) {
            return (
              <button
                key={r.name}
                onClick={() => onSelect(r.name)}
                {...(isActive ? { "data-active-source": true } : {})}
                className={`flex w-full items-center justify-center py-3 transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
                title={displayName}
              >
                {owner ? (
                  <Brain className="h-4 w-4 text-purple-400" />
                ) : hasActiveInstance ? (
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]" />
                ) : (
                  <span className="text-[10px] font-bold uppercase leading-none">
                    {displayName.slice(0, 2)}
                  </span>
                )}
              </button>
            );
          }

          return (
            <button
              key={r.name}
              onClick={() => onSelect(r.name)}
              {...(isActive ? { "data-active-source": true } : {})}
              className={`block w-full rounded-lg border p-2.5 text-left transition-all ${
                isActive
                  ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                  : "border-[var(--color-border)] bg-[var(--color-bg)] hover:border-[var(--color-primary)]/30 hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  {hasActiveInstance && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                  )}
                  <span
                    className={`truncate text-xs font-semibold ${
                      isActive ? "text-[var(--color-text)]" : "text-[var(--color-text)]/90"
                    }`}
                  >
                    {displayName}
                  </span>
                </div>
                {reportCount > 0 && (
                  <span className="shrink-0 rounded-full bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[9px] font-medium text-[var(--color-text-muted)]">
                    {reportCount}
                  </span>
                )}
              </div>
              {owner && (
                <span className="mt-1 inline-flex items-center gap-0.5 rounded bg-purple-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-purple-400">
                  <Brain className="h-2 w-2" />
                  {owner}
                </span>
              )}
              <p className="mt-1 truncate text-[10px] text-[var(--color-text-muted)]">
                {info ? info.title : r.description}
              </p>
              <div className="mt-1 flex items-center gap-1.5">
                {info ? (
                  <>
                    <span className="text-[9px] text-[var(--color-text-muted)]/60">
                      {formatRelativeTime(info.created_at)}
                    </span>
                    {info.tags.slice(0, 2).map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[8px] text-[var(--color-text-muted)]/60"
                      >
                        #{tag}
                      </span>
                    ))}
                  </>
                ) : (
                  <span className="text-[9px] italic text-[var(--color-text-muted)]/40">
                    No reports yet
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* What has been run, live or finished — the launcher page's strip, which
          was the only place a run that wrote no report could be found. */}
      {!compact && runs.length > 0 && (
        <div className="max-h-52 shrink-0 overflow-y-auto border-t border-[var(--color-border)] scrollbar-thin">
          <h3 className="sticky top-0 bg-[var(--color-surface)] px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Runs <span className="font-medium opacity-60">{runs.length}</span>
          </h3>
          <div className="px-2 pb-2">
            {shownRuns.map((inst) => (
              <div key={inst.instance_id} className="group flex items-center gap-1.5 rounded px-1 py-1 hover:bg-[var(--color-surface-hover)]">
                <button
                  onClick={() => onSelect(inst.routine_name)}
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  title={`Open ${inst.routine_name.replace(/_/g, " ")}`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      inst.error || inst.status === "failed"
                        ? "bg-[var(--color-red)]"
                        : inst.status === "running"
                          ? "animate-pulse bg-emerald-400"
                          : inst.status === "scheduled"
                            ? "bg-amber-400"
                            : "bg-sky-400"
                    }`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[10px] font-medium text-[var(--color-text)]">
                      {inst.routine_name.replace(/_/g, " ")}
                    </span>
                    <span className="flex items-center gap-1.5 text-[9px] text-[var(--color-text-muted)]">
                      <span className="capitalize">{inst.status}</span>
                      {!live(inst.status) && inst.last_run_at && (
                        <span>{formatRelativeTime(inst.last_run_at, "")}</span>
                      )}
                      {inst.schedule?.type === "interval" && (
                        <span className="flex items-center gap-0.5">
                          <Clock className="h-2 w-2" />
                          {formatInterval(inst.schedule.interval_sec as number)}
                        </span>
                      )}
                    </span>
                  </span>
                </button>
                {/* Only something still going can be stopped. A finished run
                    keeps its row — that is the point — but not the button. */}
                {live(inst.status) && (
                  <button
                    onClick={() => onStopInstance(inst.instance_id)}
                    disabled={stopping}
                    className="rounded p-1 text-[var(--color-text-muted)] opacity-0 transition-all hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)] group-hover:opacity-100"
                    title="Stop"
                  >
                    <Square className="h-2.5 w-2.5" />
                  </button>
                )}
              </div>
            ))}
            {hiddenRuns > 0 && (
              <button
                onClick={() => setShowAllRuns(true)}
                className="mt-1 w-full rounded border border-dashed border-[var(--color-border)] py-1 text-[10px] font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                +{hiddenRuns} more
              </button>
            )}
            {showAllRuns && runs.length > RUNS_COLLAPSED_LIMIT && (
              <button
                onClick={() => setShowAllRuns(false)}
                className="mt-1 w-full rounded border border-dashed border-[var(--color-border)] py-1 text-[10px] font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Show less
              </button>
            )}
          </div>
        </div>
      )}

      {/* Navigation hint */}
      {!compact && (
        <div className="shrink-0 border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
          <span className="flex items-center gap-1.5">
            <span className="flex items-center gap-0.5">
              <Kbd><ChevronUp className="h-2.5 w-2.5" /></Kbd>
              <Kbd><ChevronDown className="h-2.5 w-2.5" /></Kbd>
              <span className="ml-0.5">source</span>
            </span>
            <span className="flex items-center gap-0.5">
              <Kbd><ChevronLeft className="h-2.5 w-2.5" /></Kbd>
              <Kbd><ChevronRight className="h-2.5 w-2.5" /></Kbd>
              <span className="ml-0.5">report</span>
            </span>
            <Kbd>esc</Kbd>
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * How many run rows the strip shows before it folds — the launcher page's cap,
 * kept because a busy day is dozens of runs pushing the routines off screen.
 */
const RUNS_COLLAPSED_LIMIT = 6;

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
      {children}
    </kbd>
  );
}

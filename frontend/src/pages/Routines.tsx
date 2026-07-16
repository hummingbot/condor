import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  Clock,
  FileText,
  LayoutGrid,
  Loader2,
  RefreshCw,
  Search,
  Table2,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { RoutineTable, type RoutineRow } from "@/components/routines/RoutineTable";
import { api } from "@/lib/api";
import { formatRelativeTime, toMs } from "@/lib/formatters";
import { scheduleRoutineName } from "@/lib/routineUtils";

type SourceTypeFilter = "all" | "routine" | "agent" | string;
type ViewMode = "grid" | "table";

const VIEW_STORAGE_KEY = "routines_view_mode";

export function Routines() {
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const agentParam = searchParams.get("agent");
  const [browserSource, setBrowserSource] = useState<string | null>(null);
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceTypeFilter>(agentParam || "all");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem(VIEW_STORAGE_KEY) as ViewMode) || "grid",
  );

  const setViewMode = useCallback((mode: ViewMode) => {
    setView(mode);
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, mode);
    } catch {
      // storage unavailable
    }
  }, []);

  const { data: routines = [], isLoading: loadingRoutines } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const { data: schedulesData } = useQuery({
    queryKey: ["routine-schedules"],
    queryFn: api.getRoutineSchedules,
    refetchInterval: 30000,
  });
  const schedules = useMemo(() => schedulesData?.schedules ?? [], [schedulesData]);

  const { data: groups = [] } = useQuery({
    queryKey: ["reports-grouped"],
    queryFn: api.getReportsGrouped,
    refetchInterval: 30000,
  });

  // Build a map of source_name -> latest report info from groups
  const reportInfo = useMemo(() => {
    const map = new Map<string, { title: string; created_at: string; count: number; tags: string[] }>();
    for (const g of groups) {
      map.set(g.source_name, {
        title: g.latest_report.title,
        created_at: g.latest_report.created_at,
        count: g.total_count,
        tags: g.all_tags,
      });
    }
    return map;
  }, [groups]);

  // Extract unique agent names for sub-filters
  const agentNames = useMemo(() => {
    const names = new Set<string>();
    for (const r of routines) {
      if (r.source.startsWith("agent:")) {
        names.add(r.source.replace("agent:", ""));
      }
    }
    return Array.from(names).sort();
  }, [routines]);

  const hasAgents = routines.some((r) => r.source.startsWith("agent:"));
  const routineCount = routines.filter((r) => !r.source.startsWith("agent:")).length;
  const agentCount = routines.filter((r) => r.source.startsWith("agent:")).length;

  // Filtered routines
  const filteredRoutines = useMemo(() => {
    let result = routines;

    if (sourceTypeFilter === "routine") {
      result = result.filter((r) => !r.source.startsWith("agent:"));
    } else if (sourceTypeFilter === "agent") {
      result = result.filter((r) => r.source.startsWith("agent:"));
    } else if (sourceTypeFilter !== "all") {
      result = result.filter((r) => r.source === `agent:${sourceTypeFilter}`);
    }

    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.description.toLowerCase().includes(q) ||
          r.category.toLowerCase().includes(q),
      );
    }

    return result;
  }, [routines, sourceTypeFilter, search]);

  // Latest fire time (epoch ms) per routine, from schedules.
  const lastRunByRoutine = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of schedules) {
      if (!s.last_fired) continue;
      const name = scheduleRoutineName(s);
      const ms = toMs(s.last_fired);
      const prev = map.get(name) ?? 0;
      if (ms > prev) map.set(name, ms);
    }
    return map;
  }, [schedules]);

  // Enriched rows for the table view.
  const tableRows = useMemo<RoutineRow[]>(() => {
    return filteredRoutines.map((r) => {
      const isAgent = r.source.startsWith("agent:");
      const info = reportInfo.get(r.name);
      const reportMs = info ? toMs(info.created_at) : 0;
      const runMs = lastRunByRoutine.get(r.name) ?? 0;
      const lastExecuted = Math.max(reportMs, runMs) || null;
      return {
        name: r.name,
        displayName: r.name.replace(/_/g, " "),
        description: info?.title ?? r.description,
        isAgent,
        agentName: isAgent ? r.source.replace("agent:", "") : null,
        isContinuous: r.is_continuous,
        category: r.category,
        reportCount: info?.count ?? r.report_count,
        lastModified: r.last_modified,
        lastExecuted,
        hasActive: schedules.some(
          (s) => scheduleRoutineName(s) === r.name && !s.disabled,
        ),
      };
    });
  }, [filteredRoutines, reportInfo, lastRunByRoutine, schedules]);

  const removeScheduleMutation = useMutation({
    mutationFn: (id: string) => api.removeRoutineSchedule(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-schedules"] });
    },
  });

  const refreshAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["routines"] });
    qc.invalidateQueries({ queryKey: ["routine-schedules"] });
    qc.invalidateQueries({ queryKey: ["reports-grouped"] });
  }, [qc]);

  return (
    <>
      <div className="space-y-4">
        {/* Header row: filters + search + refresh */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setSourceTypeFilter("all")}
              className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                sourceTypeFilter === "all"
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              All {routines.length}
            </button>
            {routineCount > 0 && (
              <button
                onClick={() => setSourceTypeFilter("routine")}
                className={`shrink-0 flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                  sourceTypeFilter === "routine"
                    ? "bg-[var(--color-primary)] text-white"
                    : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                <Zap className="h-3 w-3" />
                Routines {routineCount}
              </button>
            )}
            {hasAgents && (
              <button
                onClick={() => setSourceTypeFilter("agent")}
                className={`shrink-0 flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                  sourceTypeFilter === "agent"
                    ? "bg-purple-500 text-white"
                    : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                <Brain className="h-3 w-3" />
                Agents {agentCount}
              </button>
            )}
            {agentNames.map((name) => (
              <button
                key={name}
                onClick={() => setSourceTypeFilter(name)}
                className={`shrink-0 flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
                  sourceTypeFilter === name
                    ? "bg-purple-500 text-white"
                    : "bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
                }`}
              >
                <Brain className="h-2.5 w-2.5" />
                {name}
              </button>
            ))}
          </div>

          <div className="relative max-w-xs flex-1 ml-auto">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search..."
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-9 pr-8 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <div className="flex items-center rounded-md border border-[var(--color-border)] p-0.5">
            <button
              onClick={() => setViewMode("grid")}
              className={`rounded p-1.5 transition-colors ${
                view === "grid"
                  ? "bg-[var(--color-surface-hover)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
              title="Grid view"
            >
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button
              onClick={() => setViewMode("table")}
              className={`rounded p-1.5 transition-colors ${
                view === "table"
                  ? "bg-[var(--color-surface-hover)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
              title="Table view"
            >
              <Table2 className="h-4 w-4" />
            </button>
          </div>

          <button
            onClick={refreshAll}
            className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            title="Refresh"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {/* Schedules strip */}
        {schedules.length > 0 && (
          <div>
            <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Scheduled
            </h2>
            <div className="flex flex-wrap gap-2">
              {schedules.map((s) => {
                const name = scheduleRoutineName(s);
                return (
                  <div
                    key={s.schedule_id}
                    className="group flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 transition-all hover:border-[var(--color-primary)]/30"
                  >
                    <button
                      onClick={() => setBrowserSource(name)}
                      className="flex items-center gap-2 text-left"
                    >
                      <span
                        className={`h-2 w-2 rounded-full shrink-0 ${
                          s.disabled ? "bg-amber-400" : "bg-emerald-400 animate-pulse"
                        }`}
                      />
                      <div>
                        <span className="text-xs font-medium text-[var(--color-text)]">
                          {name.replace(/_/g, " ")}
                        </span>
                        <div className="flex items-center gap-2 text-[9px] text-[var(--color-text-muted)]">
                          <span className="flex items-center gap-0.5 font-mono">
                            <Clock className="h-2 w-2" />
                            {s.cron}
                          </span>
                          {s.disabled ? (
                            <span className="max-w-48 truncate text-amber-400">
                              disabled{s.disabled_reason ? `: ${s.disabled_reason}` : ""}
                            </span>
                          ) : (
                            s.last_fired && (
                              <span>fired {formatRelativeTime(s.last_fired)}</span>
                            )
                          )}
                        </div>
                      </div>
                    </button>
                    <button
                      onClick={() => removeScheduleMutation.mutate(s.schedule_id)}
                      disabled={removeScheduleMutation.isPending}
                      className="rounded p-1 text-[var(--color-text-muted)] opacity-0 group-hover:opacity-100 hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)] transition-all"
                      title="Remove schedule"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Routine cards grid */}
        {loadingRoutines ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
          </div>
        ) : filteredRoutines.length === 0 ? (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] px-6 py-12 text-center">
            <FileText className="mx-auto mb-2 h-6 w-6 text-[var(--color-text-muted)]/30" />
            <p className="text-sm text-[var(--color-text-muted)]">
              {routines.length === 0 ? "No routines available" : "No matches"}
            </p>
          </div>
        ) : view === "table" ? (
          <RoutineTable rows={tableRows} onOpen={setBrowserSource} />
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredRoutines.map((r) => {
              const isAgent = r.source.startsWith("agent:");
              const info = reportInfo.get(r.name);
              const routineSchedules = schedules.filter(
                (s) => scheduleRoutineName(s) === r.name,
              );
              const liveSchedule = routineSchedules.find((s) => !s.disabled);
              const disabledSchedule = liveSchedule
                ? undefined
                : routineSchedules.find((s) => s.disabled);
              return (
                <button
                  key={r.name}
                  onClick={() => setBrowserSource(r.name)}
                  className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-all hover:border-[var(--color-primary)]/30 hover:scale-[1.01]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        {liveSchedule && (
                          <span
                            className="flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[8px] text-emerald-400"
                            title={`cron ${liveSchedule.cron} (${liveSchedule.tz})`}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                            {liveSchedule.cron}
                          </span>
                        )}
                        {disabledSchedule && (
                          <span
                            className="max-w-24 shrink-0 truncate rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[8px] text-amber-400"
                            title={disabledSchedule.disabled_reason || "schedule disabled"}
                          >
                            disabled{disabledSchedule.disabled_reason ? `: ${disabledSchedule.disabled_reason}` : ""}
                          </span>
                        )}
                        <p className="text-xs font-semibold text-[var(--color-text)] truncate">
                          {r.name.replace(/_/g, " ")}
                        </p>
                      </div>
                      {isAgent && (
                        <span className="mt-1 inline-flex items-center gap-0.5 rounded bg-purple-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-purple-400">
                          <Brain className="h-2 w-2" />
                          {r.source.replace("agent:", "")}
                        </span>
                      )}
                    </div>
                    {(info?.count ?? r.report_count) > 0 && (
                      <span className="shrink-0 rounded-full bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[9px] font-medium text-[var(--color-text-muted)]">
                        {info?.count ?? r.report_count}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-[11px] text-[var(--color-text-muted)] truncate">
                    {info ? info.title : r.description}
                  </p>
                  <div className="mt-1.5 flex items-center gap-1.5">
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
                      <span className="text-[9px] text-[var(--color-text-muted)]/40 italic">
                        No reports yet
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Report Browser overlay */}
      {browserSource !== null && (
        <ReportBrowser
          initialSource={browserSource}
          initialSourceTypeFilter={sourceTypeFilter}
          onClose={() => setBrowserSource(null)}
        />
      )}
    </>
  );
}

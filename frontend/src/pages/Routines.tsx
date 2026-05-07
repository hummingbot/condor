import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  Clock,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  Square,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

type SourceTypeFilter = "all" | "routine" | "agent" | string;

export function Routines() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [browserSource, setBrowserSource] = useState<string | null>(null);
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceTypeFilter>("all");
  const [search, setSearch] = useState("");

  const { data: routines = [], isLoading: loadingRoutines } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

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

  const activeInstances = useMemo(
    () => instances.filter((i) => i.status === "running" || i.status === "scheduled"),
    [instances],
  );

  const stopMutation = useMutation({
    mutationFn: (id: string) => api.stopRoutineInstance(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const refreshAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["routines"] });
    qc.invalidateQueries({ queryKey: ["routine-instances"] });
    qc.invalidateQueries({ queryKey: ["reports-grouped"] });
  }, [qc]);

  if (!server) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
        Select a server to view routines
      </div>
    );
  }

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

          <button
            onClick={refreshAll}
            className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            title="Refresh"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {/* Active instances strip */}
        {activeInstances.length > 0 && (
          <div>
            <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Active
            </h2>
            <div className="flex flex-wrap gap-2">
              {activeInstances.map((inst) => (
                <div
                  key={inst.instance_id}
                  className="group flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 transition-all hover:border-[var(--color-primary)]/30"
                >
                  <button
                    onClick={() => setBrowserSource(inst.routine_name)}
                    className="flex items-center gap-2 text-left"
                  >
                    <span
                      className={`h-2 w-2 rounded-full shrink-0 ${
                        inst.status === "running"
                          ? "bg-emerald-400 animate-pulse"
                          : "bg-amber-400"
                      }`}
                    />
                    <div>
                      <span className="text-xs font-medium text-[var(--color-text)]">
                        {inst.routine_name.replace(/_/g, " ")}
                      </span>
                      <div className="flex items-center gap-2 text-[9px] text-[var(--color-text-muted)]">
                        <span className="capitalize">{inst.status}</span>
                        {inst.schedule?.type === "interval" && (
                          <span className="flex items-center gap-0.5">
                            <Clock className="h-2 w-2" />
                            {inst.schedule.interval_sec as number}s
                          </span>
                        )}
                        {inst.run_count > 0 && <span>{inst.run_count} runs</span>}
                      </div>
                    </div>
                  </button>
                  <button
                    onClick={() => stopMutation.mutate(inst.instance_id)}
                    disabled={stopMutation.isPending}
                    className="rounded p-1 text-[var(--color-text-muted)] opacity-0 group-hover:opacity-100 hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)] transition-all"
                    title="Stop"
                  >
                    <Square className="h-3 w-3" />
                  </button>
                </div>
              ))}
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
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredRoutines.map((r) => {
              const isAgent = r.source.startsWith("agent:");
              const info = reportInfo.get(r.name);
              const hasActive = instances.some(
                (i) => i.routine_name === r.name && (i.status === "running" || i.status === "scheduled"),
              );
              return (
                <button
                  key={r.name}
                  onClick={() => setBrowserSource(r.name)}
                  className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-all hover:border-[var(--color-primary)]/30 hover:scale-[1.01]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        {hasActive && (
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
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
                          {formatAgo(info.created_at)}
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
          instances={instances}
          onClose={() => setBrowserSource(null)}
        />
      )}
    </>
  );
}

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

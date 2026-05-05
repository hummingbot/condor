import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Clock,
  Loader2,
  Play,
  RefreshCw,
  Square,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { RoutineConfigForm } from "@/components/routines/RoutineConfigForm";
import { RoutineResultView } from "@/components/routines/RoutineResultView";
import { ScheduleDropdown } from "@/components/routines/ScheduleDropdown";
import { useServer } from "@/hooks/useServer";
import { type RoutineInfo, api } from "@/lib/api";

function formatDuration(sec: number | null): string {
  if (sec == null) return "-";
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
}

function formatAgo(ts: number | null): string {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function Routines() {
  const { server } = useServer();
  const qc = useQueryClient();

  const [selected, setSelected] = useState<string | null>(null);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  // Track the instance we're waiting on (or showing results for)
  const [activeInstanceId, setActiveInstanceId] = useState<string | null>(null);
  const isPolling = useRef(false);

  // ── Queries ──

  const { data: routines = [], isLoading: loadingRoutines } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

  // Fetch the active instance (for polling while running + showing result after)
  const { data: activeInstance } = useQuery({
    queryKey: ["routine-instance", activeInstanceId],
    queryFn: () => api.getRoutineInstance(activeInstanceId!),
    enabled: !!activeInstanceId,
    // Poll every 2s while running, stop once done
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && status !== "running") return false;
      return 2000;
    },
  });

  // When active instance completes, refresh the instances list
  const prevStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = activeInstance?.status;
    if (prevStatus.current === "running" && status && status !== "running") {
      isPolling.current = false;
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    }
    prevStatus.current = status;
  }, [activeInstance?.status, qc]);

  // ── Selected routine ──

  const selectedRoutine = useMemo(
    () => routines.find((r) => r.name === selected),
    [routines, selected],
  );

  const selectedInstances = useMemo(
    () => instances.filter((i) => i.routine_name === selected),
    [instances, selected],
  );

  const handleSelect = useCallback(
    (routine: RoutineInfo) => {
      setSelected(routine.name);
      const defaults: Record<string, unknown> = {};
      for (const [key, field] of Object.entries(routine.fields)) {
        defaults[key] = field.default;
      }
      setConfigValues(defaults);
      setActiveInstanceId(null);
      isPolling.current = false;
    },
    [],
  );

  const handleConfigChange = useCallback((key: string, value: unknown) => {
    setConfigValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // ── Mutations ──

  const runMutation = useMutation({
    mutationFn: () => api.runRoutine(server!, selected!, configValues),
    onSuccess: (data) => {
      isPolling.current = true;
      setActiveInstanceId(data.instance_id);
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      api.scheduleRoutine(server!, selected!, configValues, intervalSec),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: string) => api.stopRoutineInstance(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  // ── Stats ──

  const runningCount = instances.filter(
    (i) => i.status === "running" || i.status === "scheduled",
  ).length;

  if (!server) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
        Select a server to view routines
      </div>
    );
  }

  const isRunning = isPolling.current && (!activeInstance || activeInstance.status === "running");

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)]">Routines</h1>
          <p className="text-xs text-[var(--color-text-muted)]">
            {routines.length} available · {runningCount} running
          </p>
        </div>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ["routines"] })}
          className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          title="Refresh"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      <div className="flex flex-col md:flex-row gap-4" style={{ minHeight: "calc(100vh - 160px)" }}>
        {/* ── Left: Catalog ── */}
        <div className={`w-full md:w-72 shrink-0 space-y-2 ${selected ? "hidden md:block" : "block"}`}>
          {loadingRoutines ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
            </div>
          ) : (
            routines.map((r) => {
              const isActive = r.name === selected;
              const hasRunning = instances.some(
                (i) => i.routine_name === r.name && (i.status === "running" || i.status === "scheduled"),
              );
              return (
                <button
                  key={r.name}
                  onClick={() => handleSelect(r)}
                  className={`w-full rounded-lg border p-3 text-left transition-all ${
                    isActive
                      ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                      : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/20"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-[var(--color-text)]">
                      {r.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                    </span>
                    <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                      {r.is_continuous ? (
                        <span className="flex items-center gap-1">
                          <Activity className="h-3 w-3" /> Loop
                        </span>
                      ) : (
                        <span className="flex items-center gap-1">
                          <Zap className="h-3 w-3" /> One-shot
                        </span>
                      )}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-[var(--color-text-muted)] line-clamp-2">
                    {r.description}
                  </p>
                  {hasRunning && (
                    <span className="mt-2 inline-flex items-center gap-1 rounded bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                      Running
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>

        {/* ── Right: Detail ── */}
        <div className={`flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 md:p-5 ${!selected ? "hidden md:block" : "block"}`}>
          {!selectedRoutine ? (
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
              Select a routine from the catalog
            </div>
          ) : (
            <div className="space-y-5">
              {/* Mobile Back Button */}
              <button
                onClick={() => setSelected(null)}
                className="flex items-center gap-2 text-xs font-medium text-[var(--color-primary)] md:hidden mb-2"
              >
                <RefreshCw className="h-3 w-3 rotate-180" /> Back to Catalog
              </button>

              {/* Title */}
              <div>
                <h2 className="text-lg font-semibold text-[var(--color-text)]">
                  {selectedRoutine.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                </h2>
                <p className="text-xs text-[var(--color-text-muted)]">
                  {selectedRoutine.description}
                </p>
              </div>

              {/* Config Form */}
              {Object.keys(selectedRoutine.fields).length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                    Configuration
                  </h3>
                  <RoutineConfigForm
                    fields={selectedRoutine.fields}
                    values={configValues}
                    onChange={handleConfigChange}
                  />
                </div>
              )}

              {/* Actions */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={runMutation.isPending || isRunning}
                  onClick={() => runMutation.mutate()}
                  className="flex items-center gap-1.5 rounded bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                >
                  {runMutation.isPending || isRunning ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                  {isRunning ? "Running..." : "Run Now"}
                </button>
                {!selectedRoutine.is_continuous && (
                  <ScheduleDropdown
                    onSchedule={(sec) => scheduleMutation.mutate(sec)}
                    disabled={scheduleMutation.isPending}
                  />
                )}
              </div>

              {runMutation.isError && (
                <p className="text-xs text-[var(--color-red)]">
                  {(runMutation.error as Error).message}
                </p>
              )}

              {/* Running spinner */}
              {isRunning && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Executing...
                </div>
              )}

              {/* Result display — persists because activeInstanceId stays set */}
              {activeInstance && activeInstance.status !== "running" && (activeInstance.result_text || activeInstance.has_result) && (
                <div className="overflow-x-auto">
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                    Last Result
                  </h3>
                  <RoutineResultView instance={activeInstance} />
                </div>
              )}

              {/* Active instances */}
              {selectedInstances.length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                    Active Instances
                  </h3>
                  <div className="space-y-2">
                    {selectedInstances.map((inst) => (
                      <div
                        key={inst.instance_id}
                        className="flex flex-col sm:flex-row sm:items-center justify-between rounded-md border border-[var(--color-border)] bg-[var(--color-surface-hover)]/50 px-3 py-2 gap-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                          <code className="text-xs font-mono text-[var(--color-primary)]">
                            #{inst.instance_id.slice(0, 8)}
                          </code>
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                              inst.status === "running" || inst.status === "scheduled"
                                ? "bg-emerald-500/10 text-emerald-400"
                                : inst.status === "completed"
                                  ? "bg-blue-500/10 text-blue-400"
                                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                            }`}
                          >
                            {inst.status}
                          </span>
                          {inst.schedule?.type === "interval" && (
                            <span className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)]">
                              <Clock className="h-3 w-3" />
                              {(inst.schedule.interval_sec as number)}s
                            </span>
                          )}
                          <span className="text-[10px] text-[var(--color-text-muted)]">
                            {inst.run_count} runs · {formatAgo(inst.last_run_at)}
                          </span>
                        </div>
                        <div className="flex items-center justify-end gap-3">
                          {inst.last_duration != null && (
                            <span className="text-[10px] text-[var(--color-text-muted)]">
                              {formatDuration(inst.last_duration)}
                            </span>
                          )}
                          <button
                            type="button"
                            onClick={() => stopMutation.mutate(inst.instance_id)}
                            disabled={stopMutation.isPending}
                            className="rounded p-1 text-[var(--color-red)] hover:bg-[var(--color-red)]/10"
                            title="Stop"
                          >
                            <Square className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

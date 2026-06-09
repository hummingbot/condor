import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Brain, ExternalLink, FileText, Loader2, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { type RoutineInfo, type RoutineInstance, api } from "@/lib/api";
import { buildConfigValues, formatRoutineName, invalidateRoutineQueries, saveConfig } from "@/lib/routineUtils";
import { useServer } from "@/hooks/useServer";

import { RoutineConfigForm } from "./RoutineConfigForm";
import { RoutineHooksPanel } from "./RoutineHooksPanel";
import { RoutineInstances } from "./RoutineInstances";
import { RoutineReports } from "./RoutineReports";
import { RoutineResultView } from "./RoutineResultView";
import { ScheduleDropdown } from "./ScheduleDropdown";

interface RoutineDetailProps {
  routine: RoutineInfo;
  instances: RoutineInstance[];
  onOpenReport?: (routineName: string) => void;
}

export function RoutineDetail({ routine, instances, onOpenReport }: RoutineDetailProps) {
  const { server } = useServer();
  const qc = useQueryClient();

  const [configValues, setConfigValues] = useState<Record<string, unknown>>(() =>
    buildConfigValues(routine),
  );
  const [activeInstanceId, setActiveInstanceId] = useState<string | null>(null);
  const isPolling = useRef(false);

  // Rebuild config when routine changes — merge saved values with current fields
  useEffect(() => {
    setConfigValues(buildConfigValues(routine));
    setActiveInstanceId(null);
    isPolling.current = false;
  }, [routine.name]);

  // Persist config to localStorage whenever it changes
  const handleConfigChange = useCallback(
    (key: string, value: unknown) => {
      setConfigValues((prev) => {
        const next = { ...prev, [key]: value };
        saveConfig(routine.name, next);
        return next;
      });
    },
    [routine.name],
  );

  // Poll active instance
  const { data: activeInstance } = useQuery({
    queryKey: ["routine-instance", activeInstanceId],
    queryFn: () => api.getRoutineInstance(activeInstanceId!),
    enabled: !!activeInstanceId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && status !== "running") return false;
      return 2000;
    },
  });

  const prevStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = activeInstance?.status;
    if (prevStatus.current === "running" && status && status !== "running") {
      isPolling.current = false;
      invalidateRoutineQueries(qc, routine.name);
    }
    prevStatus.current = status;
  }, [activeInstance?.status, qc, routine.name]);

  const runMutation = useMutation({
    mutationFn: () => api.runRoutine(server!, routine.name, configValues),
    onSuccess: (data) => {
      isPolling.current = true;
      setActiveInstanceId(data.instance_id);
      invalidateRoutineQueries(qc, routine.name);
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      api.scheduleRoutine(server!, routine.name, configValues, intervalSec),
    onSuccess: () => {
      invalidateRoutineQueries(qc, routine.name);
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: string) => api.stopRoutineInstance(id),
    onSuccess: () => {
      invalidateRoutineQueries(qc, routine.name);
    },
  });

  const isRunning = isPolling.current && (!activeInstance || activeInstance.status === "running");
  const isAgent = routine.source.startsWith("agent:");
  const selectedInstances = instances.filter((i) => i.routine_name === routine.name);

  return (
    <div className="space-y-5">
      {/* Title */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">
            {formatRoutineName(routine.name)}
          </h2>
          {isAgent && (
            <span className="flex items-center gap-1 rounded bg-purple-500/10 px-2 py-0.5 text-[10px] font-bold text-purple-400">
              <Brain className="h-3 w-3" />
              {routine.source.replace("agent:", "")}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          {routine.description}
        </p>
      </div>

      {/* Config Form */}
      {Object.keys(routine.fields).length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Configuration
          </h3>
          <RoutineConfigForm
            fields={routine.fields}
            values={configValues}
            onChange={handleConfigChange}
          />
        </div>
      )}

      {/* Post-execution notifications */}
      <RoutineHooksPanel routineName={routine.name} />

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={runMutation.isPending || isRunning || !server}
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
        {!routine.is_continuous && (
          <ScheduleDropdown
            onSchedule={(sec) => scheduleMutation.mutate(sec)}
            disabled={scheduleMutation.isPending || !server}
          />
        )}
      </div>

      {runMutation.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(runMutation.error as Error).message}
        </p>
      )}

      {/* Running indicator */}
      {isRunning && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          Executing...
        </div>
      )}

      {/* Result - show error or link to report */}
      {activeInstance && activeInstance.status !== "running" && (activeInstance.result_text || activeInstance.has_result || activeInstance.error) && (
        <div>
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Last Result
          </h3>
          {activeInstance.status === "failed" || activeInstance.error ? (
            <div className="rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/5 px-4 py-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="h-4 w-4 text-[var(--color-red)] shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-[var(--color-red)]">
                    Routine failed
                  </p>
                  <pre className="mt-1.5 text-[11px] text-[var(--color-text-muted)] whitespace-pre-wrap break-words font-mono">
                    {activeInstance.error || activeInstance.result_text}
                  </pre>
                </div>
              </div>
            </div>
          ) : (
            <>
              <RoutineResultView instance={activeInstance} />
              {onOpenReport && routine.report_count > 0 && (
                <button
                  onClick={() => onOpenReport(routine.name)}
                  className="flex items-center gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 px-4 py-3 text-left transition-all hover:border-[var(--color-primary)]/50 hover:bg-[var(--color-primary)]/10 w-full mt-2"
                >
                  <FileText className="h-5 w-5 text-[var(--color-primary)] shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-[var(--color-text)]">
                      View reports
                    </p>
                  </div>
                  <ExternalLink className="h-4 w-4 text-[var(--color-primary)] shrink-0" />
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* Active instances */}
      <RoutineInstances
        instances={selectedInstances}
        onStop={(id) => stopMutation.mutate(id)}
        stopping={stopMutation.isPending}
      />

      {/* Reports section */}
      <RoutineReports
        routineName={routine.name}
        hasScheduledInstance={selectedInstances.some(i => i.status === "running" || i.status === "scheduled")}
      />
    </div>
  );
}


import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, ExternalLink, FileText, Loader2, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type RoutineInfo, type RoutineInstance, api } from "@/lib/api";
import { useServer } from "@/hooks/useServer";

import { RoutineConfigForm } from "./RoutineConfigForm";
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

  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [activeInstanceId, setActiveInstanceId] = useState<string | null>(null);
  const isPolling = useRef(false);

  // Reset config when routine changes
  useEffect(() => {
    const defaults: Record<string, unknown> = {};
    for (const [key, field] of Object.entries(routine.fields)) {
      defaults[key] = field.default;
    }
    setConfigValues(defaults);
    setActiveInstanceId(null);
    isPolling.current = false;
  }, [routine.name]);

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
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      qc.invalidateQueries({ queryKey: ["routine-reports", routine.name] });
    }
    prevStatus.current = status;
  }, [activeInstance?.status, qc, routine.name]);

  const runMutation = useMutation({
    mutationFn: () => api.runRoutine(server!, routine.name, configValues),
    onSuccess: (data) => {
      isPolling.current = true;
      setActiveInstanceId(data.instance_id);
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      api.scheduleRoutine(server!, routine.name, configValues, intervalSec),
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

  const isRunning = isPolling.current && (!activeInstance || activeInstance.status === "running");
  const isAgent = routine.source.startsWith("agent:");
  const selectedInstances = instances.filter((i) => i.routine_name === routine.name);

  return (
    <div className="space-y-5">
      {/* Title */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">
            {formatName(routine.name)}
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
            onChange={(key, value) => setConfigValues((prev) => ({ ...prev, [key]: value }))}
          />
        </div>
      )}

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

      {/* Result - show link to report instead of raw text */}
      {activeInstance && activeInstance.status !== "running" && (activeInstance.result_text || activeInstance.has_result) && (
        <div>
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Last Result
          </h3>
          {onOpenReport ? (
            <button
              onClick={() => onOpenReport(routine.name)}
              className="flex items-center gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 px-4 py-3 text-left transition-all hover:border-[var(--color-primary)]/50 hover:bg-[var(--color-primary)]/10 w-full"
            >
              <FileText className="h-5 w-5 text-[var(--color-primary)] shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-[var(--color-text)]">
                  Report generated successfully
                </p>
                <p className="text-[10px] text-[var(--color-text-muted)] mt-0.5 truncate">
                  {activeInstance.result_text?.split("\n")[0] || "Click to view the full report"}
                </p>
              </div>
              <ExternalLink className="h-4 w-4 text-[var(--color-primary)] shrink-0" />
            </button>
          ) : (
            <RoutineResultView instance={activeInstance} />
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
      <RoutineReports routineName={routine.name} />
    </div>
  );
}

function formatName(name: string): string {
  const display = name.includes("/") ? name.split("/")[1] : name;
  return display.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

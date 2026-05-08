import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  Play,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type ReportSummary, type RoutineInfo, api } from "@/lib/api";
import { useServer } from "@/hooks/useServer";
import { RoutineConfigForm } from "@/components/routines/RoutineConfigForm";
import { RoutineResultView } from "@/components/routines/RoutineResultView";

// ── Helpers ──

function formatName(name: string): string {
  const display = name.includes("/") ? name.split("/").pop()! : name;
  return display.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Routine Card ──

function RoutineCard({ routine }: { routine: RoutineInfo }) {
  const { server } = useServer();
  const qc = useQueryClient();
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [expanded, setExpanded] = useState(false);
  const [activeInstanceId, setActiveInstanceId] = useState<string | null>(null);
  const isPolling = useRef(false);

  // Reset config when routine changes
  useEffect(() => {
    const defaults: Record<string, unknown> = {};
    for (const [key, field] of Object.entries(routine.fields)) {
      defaults[key] = field.default;
    }
    setConfigValues(defaults);
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
      // Invalidate agent reports so new report shows up
      qc.invalidateQueries({ queryKey: ["agent-reports"] });
    }
    prevStatus.current = status;
  }, [activeInstance?.status, qc]);

  const runMutation = useMutation({
    mutationFn: () => api.runRoutine(server!, routine.name, configValues),
    onSuccess: (data) => {
      isPolling.current = true;
      setActiveInstanceId(data.instance_id);
    },
  });

  const isRunning =
    isPolling.current && (!activeInstance || activeInstance.status === "running");
  const hasFields = Object.keys(routine.fields).length > 0;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      {/* Header row */}
      <div className="flex items-center justify-between px-4 py-3">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex-1 text-left"
        >
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            {formatName(routine.name)}
          </h3>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            {routine.description}
          </p>
        </button>
        <button
          type="button"
          disabled={runMutation.isPending || isRunning || !server}
          onClick={() => runMutation.mutate()}
          className="ml-3 flex items-center gap-1.5 rounded bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {runMutation.isPending || isRunning ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          {isRunning ? "Running..." : "Run"}
        </button>
      </div>

      {/* Expanded config */}
      {expanded && hasFields && (
        <div className="border-t border-[var(--color-border)] px-4 py-3">
          <RoutineConfigForm
            fields={routine.fields}
            values={configValues}
            onChange={(key, value) =>
              setConfigValues((prev) => ({ ...prev, [key]: value }))
            }
          />
        </div>
      )}

      {/* Error */}
      {runMutation.isError && (
        <div className="border-t border-[var(--color-border)] px-4 py-2">
          <p className="text-xs text-[var(--color-red)]">
            {(runMutation.error as Error).message}
          </p>
        </div>
      )}

      {/* Running indicator */}
      {isRunning && (
        <div className="border-t border-[var(--color-border)] px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" />
            Executing...
          </div>
        </div>
      )}

      {/* Result */}
      {activeInstance &&
        activeInstance.status !== "running" &&
        (activeInstance.result_text || activeInstance.has_result) && (
          <div className="border-t border-[var(--color-border)] px-4 py-3">
            <RoutineResultView instance={activeInstance} />
          </div>
        )}
    </div>
  );
}

// ── Reports Section ──

function AgentReports({ slug }: { slug: string }) {
  const [viewReport, setViewReport] = useState<ReportSummary | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["agent-reports", slug],
    queryFn: () => api.getAgentReports(slug),
    enabled: !!slug,
  });

  const reports = data?.reports ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" /> Loading reports...
      </div>
    );
  }

  if (reports.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[var(--color-border)] px-4 py-6 text-center">
        <FileText className="mx-auto mb-1.5 h-5 w-5 text-[var(--color-text-muted)]/40" />
        <p className="text-xs text-[var(--color-text-muted)]">
          No reports yet — run a routine to generate one
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* Report cards */}
      <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-3">
        {reports.slice(0, 9).map((r) => (
          <button
            key={r.id}
            onClick={() => setViewReport(r)}
            className={`rounded-md border p-2.5 text-left transition-all ${
              viewReport?.id === r.id
                ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                : "border-[var(--color-border)] hover:border-[var(--color-primary)]/20"
            }`}
          >
            <p className="line-clamp-1 text-xs font-medium text-[var(--color-text)]">
              {r.title}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-[10px] text-[var(--color-text-muted)]">
                {formatAgo(r.created_at)}
              </span>
              {r.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[9px] text-[var(--color-text-muted)]"
                >
                  #{tag}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>

      {/* Inline viewer */}
      {viewReport && (
        <div
          className={`overflow-hidden rounded-lg border border-[var(--color-border)] ${
            fullscreen ? "fixed inset-0 z-50 rounded-none" : ""
          }`}
        >
          <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <span className="truncate text-xs font-medium text-[var(--color-text)]">
              {viewReport.title}
            </span>
            <button
              onClick={() => setFullscreen((f) => !f)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            >
              {fullscreen ? (
                <Minimize2 className="h-3.5 w-3.5" />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          <iframe
            src={`/reports/${viewReport.filename}`}
            className={`w-full border-0 ${fullscreen ? "h-[calc(100vh-40px)]" : "h-[560px]"}`}
            title={viewReport.title}
          />
        </div>
      )}
    </div>
  );
}

// ── Main Tab ──

interface AgentRoutinesTabProps {
  slug: string;
}

export function AgentRoutinesTab({ slug }: AgentRoutinesTabProps) {
  const { data: routines, isLoading } = useQuery({
    queryKey: ["agent-routines", slug],
    queryFn: () => api.getAgentRoutines(slug),
    enabled: !!slug,
  });

  if (isLoading) {
    return (
      <div className="flex h-32 items-center justify-center text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const hasRoutines = routines && routines.length > 0;

  return (
    <div className="space-y-6">
      {/* Routines */}
      <div>
        <h2 className="mb-3 text-sm font-bold text-[var(--color-text)]">
          Routines
        </h2>
        {hasRoutines ? (
          <div className="space-y-3">
            {routines.map((r) => (
              <RoutineCard key={r.name} routine={r} />
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-[var(--color-border)] px-4 py-8 text-center">
            <p className="text-xs text-[var(--color-text-muted)]">
              No routines yet — create one in{" "}
              <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5">
                trading_agents/{slug}/routines/
              </code>
            </p>
          </div>
        )}
      </div>

      {/* Reports */}
      <div>
        <h2 className="mb-3 text-sm font-bold text-[var(--color-text)]">
          Reports
        </h2>
        <AgentReports slug={slug} />
      </div>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import { type ExperimentInfo, api } from "@/lib/api";
import { type ParsedSnapshot, parseSnapshot } from "@/lib/parse-agent";

export function ExperimentsTab({ slug, experiments }: { slug: string; experiments: ExperimentInfo[] }) {
  const [selectedExpNum, setSelectedExpNum] = useState<number>(
    experiments.length > 0 ? experiments[0].number : 0
  );

  const { data: snapshotData, isLoading } = useQuery({
    queryKey: ["agent", slug, "experiment", selectedExpNum],
    queryFn: () => api.getExperiment(slug, selectedExpNum),
    enabled: selectedExpNum > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!snapshotData?.content) return null;
    return parseSnapshot(snapshotData.content);
  }, [snapshotData?.content]);

  if (experiments.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)]">
        <FlaskConical className="mb-2 h-8 w-8 opacity-30" />
        <p className="text-sm">No experiments yet. Run a dry-run or run-once to create one.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Experiment list */}
      <div className="w-full shrink-0 lg:w-80">
        <div className="max-h-[700px] space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
          {experiments.map((exp) => {
            const isActive = exp.number === selectedExpNum;
            const modeLabel = exp.execution_mode === "dry_run" ? "Dry Run" : exp.execution_mode === "run_once" ? "Run Once" : exp.execution_mode;
            const modeColor = exp.execution_mode === "dry_run"
              ? "bg-amber-500/10 text-amber-400"
              : "bg-blue-500/10 text-blue-400";
            return (
              <button
                key={exp.number}
                onClick={() => setSelectedExpNum(exp.number)}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                  isActive ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]" : "bg-[var(--color-border)]/50 text-[var(--color-text-muted)]"
                }`}>
                  {exp.number}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">Experiment {exp.number}</span>
                    <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-medium ${modeColor}`}>
                      {modeLabel}
                    </span>
                    {exp.agent_key && (
                      <span className="inline-flex rounded-full bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-primary)]">
                        {exp.agent_key}
                      </span>
                    )}
                  </div>
                </div>
                {isActive && <div className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-primary)]" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Snapshot detail */}
      <div className="min-w-0 flex-1">
        {isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
          </div>
        ) : parsed ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-lg font-bold text-[var(--color-text)]">Experiment #{selectedExpNum}</span>
              <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
            </div>
            {parsed.agentResponse && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Agent Response</h3>
                <div className="whitespace-pre-wrap text-sm text-[var(--color-text)]">{parsed.agentResponse}</div>
              </div>
            )}
            {parsed.toolCalls.length > 0 && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Tool Calls ({parsed.toolCalls.length})
                </h3>
                <div className="space-y-2">
                  {parsed.toolCalls.map((tc, i) => (
                    <div key={i} className="rounded-md bg-[var(--color-bg)]/50 px-3 py-2 text-xs">
                      <span className="font-medium text-[var(--color-primary)]">{tc.name}</span>
                      <span className="ml-2 text-[var(--color-text-muted)]">({tc.status})</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {parsed.riskState && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Risk State</h3>
                <div className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">{parsed.riskState}</div>
              </div>
            )}
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select an experiment to view its snapshot.</p>
        )}
      </div>
    </div>
  );
}

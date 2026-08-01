import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { RoutineResultView } from "@/components/routines/RoutineResultView";
import { api, type RoutineInstance } from "@/lib/api";
import { formatRelativeTime } from "@/lib/formatters";
import { formatRoutineName } from "@/lib/routineUtils";

/**
 * What the agent on the other end has been running.
 *
 * Scoped by agent, not by conversation: routine runs carry no conversation
 * provenance (the store is shared with the scheduler and Telegram), and "this
 * agent's recent runs" is the question the dock is being asked. When the
 * conversation is unbound — the Condor assistant — the same list runs
 * unfiltered, which is the user's own recent runs.
 */
export function DockRoutines({
  instances,
  agentSlug,
}: {
  instances: RoutineInstance[];
  /** Bound agent's slug, or "" for the unbound Condor conversation. */
  agentSlug: string;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  // Agent routines are stored keyed `{agent_slug}/{name}` — the same prefix the
  // per-agent routes filter on.
  const prefix = `${agentSlug}/`;
  const runs = instances
    .filter((i) => !agentSlug || i.routine_name.startsWith(prefix))
    .sort((a, b) => (b.last_run_at ?? 0) - (a.last_run_at ?? 0));

  return (
    <>
      {runs.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          {agentSlug ? "This agent has no routine runs." : "No routine runs yet."}
        </p>
      ) : (
        <div className="space-y-px">
          {runs.map((i) => (
            <button
              key={i.instance_id}
              type="button"
              onClick={() => setOpenId(i.instance_id)}
              className="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-hover)]"
              title={i.last_result || i.routine_name}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    i.error
                      ? "bg-red-400"
                      : i.status === "running"
                        ? "bg-emerald-400 animate-pulse"
                        : i.last_run_at
                          ? "bg-sky-400"
                          : "bg-[var(--color-text-muted)]/50"
                  }`}
                />
                <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--color-text)]">
                  {formatRoutineName(i.routine_name)}
                </span>
              </span>
              <span className="block truncate pl-3 text-[10px] text-[var(--color-text-muted)]">
                {formatRelativeTime(i.last_run_at, "never run")}
                {i.run_count > 0 && ` · ${i.run_count} run${i.run_count !== 1 ? "s" : ""}`}
              </span>
              {i.last_result && (
                <span className="block truncate pl-3 text-[10px] text-[var(--color-text-muted)]">
                  {i.last_result.split("\n")[0]}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {openId && (
        <RoutineRunSheet
          instance={runs.find((i) => i.instance_id === openId)!}
          onClose={() => setOpenId(null)}
        />
      )}
    </>
  );
}

/**
 * A run's full output.
 *
 * `/routines/instances` returns only the metadata and a truncated `last_result`
 * — the structured sections, tables and chart live behind the single-instance
 * route, so the sheet fetches the one run it is showing.
 */
function RoutineRunSheet({
  instance,
  onClose,
}: {
  instance: RoutineInstance;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["routine-instance", instance.instance_id],
    queryFn: () => api.getRoutineInstance(instance.instance_id),
  });
  const full = data ?? instance;

  return (
    <WorkspaceSheet
      title={formatRoutineName(instance.routine_name)}
      subtitle={formatRelativeTime(instance.last_run_at, "never run")}
      onClose={onClose}
    >
      {isLoading ? (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading run…
        </div>
      ) : full.error ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-xs text-red-300">
          {full.error}
        </pre>
      ) : full.has_result || full.result_text ? (
        <RoutineResultView instance={full} />
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">
          {full.last_result || "(no output yet)"}
        </p>
      )}
    </WorkspaceSheet>
  );
}

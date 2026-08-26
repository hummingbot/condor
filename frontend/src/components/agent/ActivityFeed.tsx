import { useQuery } from "@tanstack/react-query";
import { Loader2, Wrench } from "lucide-react";
import { useState } from "react";

import { DelegationSheet } from "@/components/agent/DelegationSheet";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { api } from "@/lib/api";

/**
 * Every task ever delegated — including the ones this process never saw.
 *
 * The live registry dies with the bot, so before this the dashboard could only
 * ever show the tasks of the current run. The records are on disk, so history
 * reads them back and opens each one in the same sheet a live task opens in.
 *
 * Scoped to one agent when given a slug (an agent's own history), fleet-wide
 * otherwise. Running tasks are included — the list is the whole story, not the
 * part the registry has forgotten.
 */
export function DelegationHistory({ agent }: { agent?: string }) {
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["delegation-history", agent ?? "all"],
    queryFn: () => api.getDelegationHistory(agent),
    // A finished delegation never changes; only a live one does.
    refetchInterval: (q) =>
      q.state.data?.delegations.some((d) => d.status === "running") ? 5000 : false,
  });

  const rows = data?.delegations ?? [];
  const open = rows.find((d) => d.task_id === openId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-1 py-3 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading history…
      </div>
    );
  }

  if (error) {
    return (
      <p className="px-1 py-3 text-xs text-red-400">Could not load the history.</p>
    );
  }

  if (rows.length === 0) {
    return (
      <p className="px-1 py-3 text-xs text-[var(--color-text-muted)]">
        {agent
          ? "This agent has never been delegated a task."
          : "No delegated tasks recorded yet."}
      </p>
    );
  }

  return (
    <>
      <div className="divide-y divide-[var(--color-border)]/40">
        {rows.map((d) => {
          const s = DELEGATION_STATUS[d.status];
          return (
            <button
              key={d.task_id}
              type="button"
              onClick={() => setOpenId(d.task_id)}
              className="flex w-full items-start gap-3 px-1 py-2 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
              title={d.task}
            >
              <span
                className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${s.dot} ${
                  d.status === "running" ? "animate-pulse" : ""
                }`}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-[var(--color-text)]">
                  {d.task.split("\n")[0] || "—"}
                </span>
                <span className="mt-0.5 flex items-center gap-2 text-[10px] text-[var(--color-text-muted)]">
                  {!agent && (
                    <span className="font-mono text-[var(--color-primary)]">
                      {d.agent}
                    </span>
                  )}
                  <span className={s.text}>{s.label.toLowerCase()}</span>
                  {formatDelegationTime(d) && <span>{formatDelegationTime(d)}</span>}
                  {!!d.tool_count && (
                    <span className="flex items-center gap-1">
                      <Wrench className="h-2.5 w-2.5" />
                      {d.tool_count}
                    </span>
                  )}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      {open && <DelegationSheet task={open} onClose={() => setOpenId(null)} />}
    </>
  );
}

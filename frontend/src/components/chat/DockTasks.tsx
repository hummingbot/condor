import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { DelegationHistory } from "@/components/agent/DelegationHistory";
import { DelegationSheet } from "@/components/agent/DelegationSheet";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { api, type Delegation } from "@/lib/api";

/** How much of the delegation record the list is showing. */
type Scope = "mine" | "all" | "history";

const SCOPES: { id: Scope; label: string; hint: string }[] = [
  { id: "mine", label: "This chat", hint: "Tasks started from this conversation" },
  { id: "all", label: "All", hint: "Every background task running in this process" },
  { id: "history", label: "History", hint: "Finished tasks, from the records on disk" },
];

/**
 * What this conversation handed off.
 *
 * Scoped by `conversation_id`, not by agent or user: two conversations with the
 * same agent are the case this exists for. Delegations with no conversation
 * behind them (Telegram-era, consult-started) and the records that outlive the
 * process are reached through the same scope switch rather than sent to another
 * page — this dock is now the only place they are reachable from.
 *
 * One switch, not three nested disclosures: stacked expandables put "history"
 * at the same visual weight as the section it belongs to, which read as a third
 * category sitting beside Tasks and Routines. These are three views of one
 * thing, so they are three positions of one control.
 */
export function DockTasks({
  delegations,
  conversationId,
  agentSlug,
}: {
  /** Every delegation in the process — the shared `["delegations"]` query. */
  delegations: Delegation[];
  /** The conversation this dock belongs to; "" before a session settles. */
  conversationId: string;
  /** Who this conversation talks to; scopes the history. "" before a slot settles. */
  agentSlug: string;
}) {
  const queryClient = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("mine");
  // The history follows the agent the rail has highlighted, because "what has
  // this one been asked to do" is the question it is opened with. Fleet-wide is
  // one click away and reachable nowhere else, so it does not simply vanish.
  const [historyAll, setHistoryAll] = useState(false);

  const mine = conversationId
    ? delegations.filter((d) => d.conversation_id === conversationId)
    : [];
  // Newest first: the thing just started is the thing being watched.
  const byNewest = (a: Delegation, b: Delegation) => b.started_at - a.started_at;
  const ordered = [...(scope === "all" ? delegations : mine)].sort(byNewest);
  const anyRunning = mine.some((d) => d.status === "running");

  // The list already refreshes on the shared 5s poll; this only re-renders in
  // between so a running task's elapsed time advances by the second. It is a
  // render trigger, not the clock — `formatDelegationTime` reads the time
  // itself, so labels stay correct after the ticking stops.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!anyRunning) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [anyRunning]);

  const stopMutation = useMutation({
    mutationFn: (taskId: string) => api.stopDelegation(taskId),
    onSuccess: () => {
      setConfirmStopId(null);
      queryClient.invalidateQueries({ queryKey: ["delegations"] });
    },
  });

  const open = ordered.find((d) => d.task_id === openId);

  return (
    <>
      {/* Sticky, because it is what tells you which list you are looking at —
          scrolling away from that answer is how "all" gets mistaken for "mine". */}
      <div className="sticky top-0 z-10 flex items-center gap-0.5 bg-[var(--color-bg)] px-2 pb-1 pt-0.5">
        {SCOPES.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setScope(s.id)}
            title={s.hint}
            className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
              scope === s.id
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {scope === "history" ? (
        <div className="px-2">
          {agentSlug && (
            <div className="flex items-center gap-2 px-1 py-1 text-[10px] text-[var(--color-text-muted)]">
              <span className="min-w-0 flex-1 truncate">
                {historyAll ? "Every agent" : agentSlug}
              </span>
              <button
                type="button"
                onClick={() => setHistoryAll((v) => !v)}
                className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5 transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                {historyAll ? `Only ${agentSlug}` : "All agents"}
              </button>
            </div>
          )}
          <DelegationHistory
            agent={historyAll ? undefined : agentSlug || undefined}
          />
        </div>
      ) : ordered.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          {scope === "all"
            ? "No background tasks running."
            : "Nothing delegated from this conversation yet."}
        </p>
      ) : (
        <div className="space-y-px">
          {ordered.map((d) => {
            const s = DELEGATION_STATUS[d.status];
            return (
              <div
                key={d.task_id}
                className="group flex items-start gap-2 px-3 py-1.5 hover:bg-[var(--color-surface-hover)]"
              >
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${s.dot} ${
                    d.status === "running" ? "animate-pulse" : ""
                  }`}
                />
                <button
                  type="button"
                  onClick={() => setOpenId(d.task_id)}
                  className="min-w-0 flex-1 text-left"
                  title={d.task}
                >
                  <span className="block truncate font-mono text-[11px] text-[var(--color-primary)]">
                    {d.agent}
                  </span>
                  <span className="block truncate text-[11px] text-[var(--color-text-muted)]">
                    {d.task.split("\n")[0] || "—"}
                  </span>
                  <span className={`text-[10px] ${s.text}`}>
                    {s.label.toLowerCase()}
                    {formatDelegationTime(d) && ` · ${formatDelegationTime(d)}`}
                  </span>
                </button>
                {d.status === "running" &&
                  (confirmStopId === d.task_id ? (
                    <button
                      type="button"
                      onClick={() => stopMutation.mutate(d.task_id)}
                      disabled={stopMutation.isPending}
                      className="mt-0.5 flex h-5 shrink-0 items-center rounded border border-red-500/30 bg-red-500/10 px-1.5 text-[10px] text-red-400 transition-colors hover:bg-red-500/20 disabled:opacity-40"
                      title="Confirm stop"
                    >
                      {stopMutation.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        "Stop?"
                      )}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmStopId(d.task_id)}
                      className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20"
                      title="Stop task"
                    >
                      <Square className="h-2.5 w-2.5" />
                    </button>
                  ))}
              </div>
            );
          })}
        </div>
      )}

      {open && (
        <DelegationSheet task={open} onClose={() => setOpenId(null)} />
      )}
    </>
  );
}

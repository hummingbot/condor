import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Loader2, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { DelegationDetail } from "@/components/agent/DelegationDetail";
import { DelegationTranscript } from "@/components/agent/DelegationTranscript";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api, type Delegation } from "@/lib/api";

/**
 * What this conversation handed off.
 *
 * Scoped by `conversation_id`, not by agent or user: two conversations with the
 * same agent are the case this exists for. Delegations with no conversation
 * behind them (Telegram-era, consult-started) are not claimed here — they are
 * counted in the footer and left to the fleet report.
 */
export function DockTasks({
  delegations,
  conversationId,
  onOpenFleet,
}: {
  /** Every delegation in the process — the shared `["delegations"]` query. */
  delegations: Delegation[];
  /** The conversation this dock belongs to; "" before a session settles. */
  conversationId: string;
  onOpenFleet: () => void;
}) {
  const queryClient = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [view, setView] = useState<"transcript" | "result">("transcript");
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);

  const mine = conversationId
    ? delegations.filter((d) => d.conversation_id === conversationId)
    : [];
  // Newest first: the thing just started is the thing being watched.
  const ordered = [...mine].sort((a, b) => b.started_at - a.started_at);
  const others = delegations.length - mine.length;
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
      {ordered.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          Nothing delegated from this conversation yet.
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
                  onClick={() => {
                    setOpenId(d.task_id);
                    // Follow the task: while it runs there is no result yet, so
                    // the transcript *is* the view. Once it is finished the
                    // answer is what the task was for, and the transcript is the
                    // follow-up question.
                    setView(d.status === "running" ? "transcript" : "result");
                  }}
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

      {/* Work that belongs to no conversation still exists — say so. */}
      {others > 0 && (
        <button
          onClick={onOpenFleet}
          className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[10px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          {others} other background task{others !== 1 ? "s" : ""}
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </button>
      )}

      {open && (
        <WorkspaceSheet
          title={open.agent}
          subtitle={`${DELEGATION_STATUS[open.status].label.toLowerCase()} · ${formatDelegationTime(open)}`}
          onClose={() => setOpenId(null)}
        >
          {/* The ask belongs to both views, so it sits above the switch rather
              than inside the one that happens to render it. */}
          <p className="mb-3 whitespace-pre-wrap text-sm text-[var(--color-text)]">
            {open.task}
          </p>
          <div className="mb-3 flex items-center gap-1 border-b border-[var(--color-border)]/50 pb-2">
            {(["transcript", "result"] as const).map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setView(id)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-all ${
                  view === id
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                }`}
              >
                {id}
              </button>
            ))}
          </div>
          {view === "transcript" ? (
            <DelegationTranscript taskId={open.task_id} />
          ) : (
            <DelegationDetail delegation={open} clamped={false} />
          )}
        </WorkspaceSheet>
      )}
    </>
  );
}

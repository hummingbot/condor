import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, History, Loader2, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { DelegationHistory } from "@/components/agent/DelegationHistory";
import { DelegationSheet } from "@/components/agent/DelegationSheet";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { api, type Delegation } from "@/lib/api";

/**
 * What this conversation handed off.
 *
 * Scoped by `conversation_id`, not by agent or user: two conversations with the
 * same agent are the case this exists for. Delegations with no conversation
 * behind them (Telegram-era, consult-started) are folded in behind a toggle
 * rather than sent to another page — this dock is now the only place they are
 * reachable from, so nothing may be left pointing somewhere that no longer
 * exists.
 */
export function DockTasks({
  delegations,
  conversationId,
}: {
  /** Every delegation in the process — the shared `["delegations"]` query. */
  delegations: Delegation[];
  /** The conversation this dock belongs to; "" before a session settles. */
  conversationId: string;
}) {
  const queryClient = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);
  const [showOthers, setShowOthers] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const mine = conversationId
    ? delegations.filter((d) => d.conversation_id === conversationId)
    : [];
  // Newest first: the thing just started is the thing being watched.
  const byNewest = (a: Delegation, b: Delegation) => b.started_at - a.started_at;
  const ordered = [...(showOthers ? delegations : mine)].sort(byNewest);
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

      {/* Work that belongs to no conversation still exists — show it here
          rather than sending the reader to a page that no longer exists. */}
      {others > 0 && (
        <button
          onClick={() => setShowOthers((v) => !v)}
          className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[10px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          {showOthers ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )}
          {others} other background task{others !== 1 ? "s" : ""}
        </button>
      )}

      {/* The live registry dies with the bot; the records do not. */}
      <button
        onClick={() => setShowHistory((v) => !v)}
        className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[10px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {showHistory ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <History className="h-3 w-3 shrink-0" />
        Everything that has run
      </button>
      {showHistory && (
        <div className="px-2 pb-1">
          <DelegationHistory />
        </div>
      )}

      {open && (
        <DelegationSheet task={open} onClose={() => setOpenId(null)} />
      )}
    </>
  );
}

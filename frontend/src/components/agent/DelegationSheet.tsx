import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { DelegationDetail } from "@/components/agent/DelegationDetail";
import { DelegationTranscript } from "@/components/agent/DelegationTranscript";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api, type Delegation, type DelegationSummary } from "@/lib/api";

/**
 * One delegation, opened: the ask, then its transcript or its result.
 *
 * Extracted from the context dock once history gained a third caller — the
 * dock, the fleet card and an agent's history all open the same thing, and the
 * one place a delegation is read should not exist in three copies.
 *
 * A caller that already holds the full record (the dock and the fleet card poll
 * `/delegations`, bodies included) passes it and nothing is fetched. A history
 * row carries no body on purpose, so the sheet fetches the record itself —
 * which is also what makes a task recorded by a long-dead process readable here.
 */
export function DelegationSheet({
  task,
  onClose,
}: {
  task: Delegation | DelegationSummary;
  onClose: () => void;
}) {
  // Follow the task: while it runs there is no result yet, so the transcript
  // *is* the view. Once it is finished the answer is what the task was for, and
  // the transcript is the follow-up question.
  const [view, setView] = useState<"transcript" | "result">(
    task.status === "running" ? "transcript" : "result",
  );

  const hasBody = "result" in task;
  const { data } = useQuery({
    queryKey: ["delegation", task.task_id],
    queryFn: () => api.getDelegation(task.task_id),
    enabled: !hasBody,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const record = hasBody ? (task as Delegation) : data;

  return (
    <WorkspaceSheet
      title={task.agent}
      subtitle={`${DELEGATION_STATUS[task.status].label.toLowerCase()} · ${formatDelegationTime(task)}`}
      onClose={onClose}
    >
      {/* The ask belongs to both views, so it sits above the switch rather
          than inside the one that happens to render it. */}
      <p className="mb-3 whitespace-pre-wrap text-sm text-[var(--color-text)]">
        {task.task}
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
        <DelegationTranscript taskId={task.task_id} />
      ) : record ? (
        <DelegationDetail delegation={record} clamped={false} />
      ) : (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading result…
        </div>
      )}
    </WorkspaceSheet>
  );
}

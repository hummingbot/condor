import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api, type DelegationSummary } from "@/lib/api";

/** A duration the store measured, in the units it is worth reading in. */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

/** One labelled block of a run's output, absent when there is nothing to show. */
function Block({
  title,
  body,
  tone = "text",
}: {
  title: string;
  body: string;
  tone?: "text" | "error";
}) {
  if (!body.trim()) return null;
  return (
    <div className="mb-4" data-code-block={title.toLowerCase()}>
      <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {title}
      </p>
      <pre
        className={`max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--color-border)]/50 bg-[var(--color-surface)] p-3 font-mono text-xs ${
          tone === "error" ? "text-red-300" : "text-[var(--color-text)]"
        }`}
      >
        {body}
      </pre>
    </div>
  );
}

/**
 * One code run, opened: what ran, then what it produced (FEAT-061).
 *
 * A sibling of `DelegationSheet` rather than a branch inside it. That component
 * renders one body and already forks once on kind; a run has four — the code,
 * its stdout, its `result` and, when it broke, the traceback — and folding a
 * second shape into it would make the common case harder to read for the rarer
 * one.
 *
 * The row it opens from carries no body (history rows never do), so the record
 * is always fetched — from the gated route, which is why a run that is not this
 * caller's simply does not load.
 */
export function CodeRunSheet({
  row,
  onClose,
}: {
  row: DelegationSummary;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["code-run", row.task_id],
    queryFn: () => api.getCodeRun(row.task_id),
  });

  return (
    <WorkspaceSheet
      title={row.task || "code run"}
      subtitle={`${row.agent} · ${DELEGATION_STATUS[row.status].label.toLowerCase()} · ${formatDelegationTime(row)}`}
      onClose={onClose}
    >
      {isLoading ? (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading the run…
        </div>
      ) : error || !data ? (
        <p className="text-xs text-red-400">
          Could not load this run — it may have aged out of the store, or it may
          not be yours to read.
        </p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-text-muted)]">
            <span data-code-duration>{formatDuration(data.duration_ms)}</span>
            {data.server && <span>· {data.server}</span>}
            {/* The run's own status word, not the feed's translation of it. */}
            <span>· {data.status}</span>
          </div>
          <Block title="Code" body={data.code} />
          <Block title="Output" body={data.stdout} />
          <Block title="Result" body={data.result} />
          <Block title="Traceback" body={data.traceback || data.error} tone="error" />
          {!data.stdout.trim() &&
            !data.result.trim() &&
            !data.traceback.trim() &&
            !data.error.trim() && (
              <p className="text-xs text-[var(--color-text-muted)]">
                The snippet ran and produced no output.
              </p>
            )}
        </>
      )}
    </WorkspaceSheet>
  );
}

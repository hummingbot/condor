import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import type { Delegation } from "@/lib/api";

/**
 * A delegation's outcome: its result as the narrative markdown it is, or its
 * error as the raw text it is.
 *
 * `showTask` adds the task and status above it — what a standalone view needs
 * and what an already-labelled list row does not.
 */
export function DelegationDetail({
  delegation: d,
  showTask = false,
  clamped,
}: {
  delegation: Delegation;
  showTask?: boolean;
  /**
   * Bound the output's height. Defaults to "yes unless the task is shown",
   * which is the list-row/standalone split — a caller that labels the task
   * itself (the dock's sheet) sets it explicitly.
   */
  clamped?: boolean;
}) {
  const s = DELEGATION_STATUS[d.status];
  const body = (d.status === "error" ? d.error : d.result)?.trim();
  // Inside a list row the output is clamped so one long result cannot bury the
  // rows below it; standalone it gets the room, and its container scrolls.
  const clamp = (clamped ?? !showTask) ? "max-h-64 overflow-auto" : "";

  return (
    <div>
      {showTask && (
        <div className="mb-4">
          <div className="mb-2 flex items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${s.dot}`} />
            <span
              className={`text-[10px] font-bold uppercase tracking-wider ${s.text}`}
            >
              {s.label}
            </span>
            <span className="text-[11px] text-[var(--color-text-muted)]">
              {formatDelegationTime(d)}
            </span>
            <span className="font-mono text-[11px] text-[var(--color-text-muted)]">
              {d.agent}
            </span>
          </div>
          <p className="whitespace-pre-wrap text-sm text-[var(--color-text)]">
            {d.task}
          </p>
        </div>
      )}

      <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {d.status === "error" ? "Error" : "Result"}
      </p>
      {d.status === "error" ? (
        // Errors are raw (stack traces / plain messages) — keep monospace.
        <pre
          className={`${clamp} whitespace-pre-wrap break-words font-mono text-xs text-red-300`}
        >
          {body || "(no output)"}
        </pre>
      ) : body ? (
        // A delegation result is the agent's narrative final answer — render markdown.
        <div className={`chat-markdown ${clamp} text-xs text-[var(--color-text)]`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">
          {d.status === "running" ? "Running…" : "(no output)"}
        </p>
      )}
    </div>
  );
}

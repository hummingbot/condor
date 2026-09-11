import { useEffect, useState } from "react";
import { Hand } from "lucide-react";

import type { PermissionRequest } from "@/hooks/useChatSocket";
import { formatToolName } from "@/lib/formatters";

/**
 * Whole seconds until `deadline`, ticking once a second; null when the
 * backend did not say. Stops ticking at zero — nothing changes after that.
 */
function useSecondsLeft(deadline?: number): number | null {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = deadline !== undefined && deadline > now;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  if (deadline === undefined) return null;
  return Math.max(0, Math.ceil(deadline - now));
}

function mmss(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * A tool call that is paused until the user answers it.
 *
 * This used to be an amber strip above the transcript with a triangle in it —
 * the same furniture as a warning — and it was read as one: users left the
 * agent "running" while it sat blocked on a click, often right after typing
 * "confirm" in chat and reasonably believing that was the approval. So it is
 * built as the opposite of a notice, closer to a terminal's permission prompt:
 *
 * - it sits on the composer, where the user's attention already is, and
 *   cannot scroll out of view;
 * - its header says the agent is paused and on whom;
 * - it previews the call itself — tool and arguments — not just a summary line;
 * - it counts down to the automatic deny, which a notice never does;
 * - it says out loud that a chat reply is not an answer. On the dashboard a
 *   message sent mid-turn steers the agent, and steering *denies* the pending
 *   call (`condor.runtime.client.prompt`), so "confirm" typed here cancels it.
 */
export function ApprovalPrompt({
  request,
  onResolve,
}: {
  request: PermissionRequest;
  onResolve: (requestId: string, approved: boolean) => void;
}) {
  const secondsLeft = useSecondsLeft(request.deadline);
  const expired = secondsLeft === 0;
  const args =
    request.input && Object.keys(request.input).length > 0
      ? JSON.stringify(request.input, null, 2)
      : null;

  return (
    <section
      role="region"
      aria-label="Approval needed"
      aria-live="assertive"
      className="mb-2 overflow-hidden rounded-lg border-2 border-[var(--color-primary)] bg-[var(--color-surface)] shadow-lg"
    >
      <header className="flex items-center gap-2 border-b border-[var(--chat-rule)] px-3 py-2">
        {!expired && (
          <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--color-primary)] opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--color-primary)]" />
          </span>
        )}
        <Hand className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
        <p className="min-w-0 flex-1 text-sm font-semibold text-[var(--color-text)]">
          {expired ? "Approval timed out" : "Paused — waiting for your approval"}
        </p>
        {secondsLeft !== null && !expired && (
          <span
            className="shrink-0 font-mono text-xs tabular-nums text-[var(--color-text-muted)]"
            title="Denied automatically when this runs out"
          >
            {mmss(secondsLeft)}
          </span>
        )}
      </header>

      <div className="space-y-2 px-3 py-2.5">
        <p className="text-xs text-[var(--color-text-muted)]">
          {request.origin ? `${request.origin} wants to run:` : "The agent wants to run:"}
        </p>
        <p className="text-sm font-medium text-[var(--color-text)]">{request.summary}</p>
        {request.tool && (
          <div className="overflow-hidden rounded-md border border-[var(--chat-rule)] bg-[var(--chat-inset)] font-mono text-xs">
            <div className="px-2.5 py-1.5 text-[var(--color-text)]">
              {formatToolName(request.tool)}
            </div>
            {args && (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all border-t border-[var(--chat-rule)] px-2.5 py-1.5 text-[var(--color-text-muted)]">
                {args}
              </pre>
            )}
          </div>
        )}
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-[var(--chat-rule)] px-3 py-2">
        {expired ? (
          <>
            <p className="min-w-0 flex-1 text-xs text-[var(--color-text-muted)]">
              Nobody answered in time, so it was not run.
            </p>
            <button
              onClick={() => onResolve(request.request_id, false)}
              className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
            >
              Dismiss
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => onResolve(request.request_id, true)}
              className="rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-semibold text-[var(--on-primary)] hover:bg-[var(--color-primary-hover)]"
            >
              Allow
            </button>
            <button
              onClick={() => onResolve(request.request_id, false)}
              className="rounded-md border border-[var(--color-border)] px-4 py-1.5 text-sm font-medium text-[var(--color-text)] hover:border-[var(--color-red)] hover:text-[var(--color-red)]"
            >
              Deny
            </button>
            <p className="min-w-0 flex-1 text-[11px] leading-snug text-[var(--color-text-muted)]">
              Nothing runs until you choose. A chat reply is not an answer —
              sending one cancels this call.
            </p>
          </>
        )}
      </footer>
    </section>
  );
}

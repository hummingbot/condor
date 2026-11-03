import { useQuery } from "@tanstack/react-query";
import { Zap } from "lucide-react";

import { api } from "@/lib/api";

// ── Session Actions (FEAT-097) ──
//
// What the session actually **did**: one row per mutating tool call, read from
// `sessions/session_{N}/actions.jsonl`.
//
// It sits above Decisions on purpose. A decision is the model's own commentary
// — it exists only when the model chose to write one, and its text is whatever
// it typed — while a row here is a tool call that ran, with an outcome the
// stream reported. The fact goes above the commentary on it.
//
// The other difference is in the path, not the pixels: `SessionActivity` reads
// `parseDecisions`, a regex over markdown a human wrote (`parse-agent.ts`).
// This reads structured JSON, so no rendering here interprets a tool's
// arguments — the summary was rendered once, in Python, by the same function
// that writes the confirmation prompt.

interface SessionActionsProps {
  slug: string;
  sslug: string;
  sessionNum: number;
  /** Jump to a tick's full snapshot. Wired to the reviewer's Snapshots tab. */
  onSnapshotClick?: (tick: number) => void;
}

export function SessionActions({
  slug,
  sslug,
  sessionNum,
  onSnapshotClick,
}: SessionActionsProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  const actions = data?.actions ?? [];

  if (isLoading) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Loading actions…
      </p>
    );
  }

  if (actions.length === 0) {
    // Two different silences read the same way here, and saying so is honest:
    // a session that only ever read, and one that ran before the log existed
    // (nothing is backfilled).
    return (
      <p
        data-action-empty
        className="py-8 text-center text-sm text-[var(--color-text-muted)]"
      >
        No actions recorded for this session.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {actions.map((action, i) => (
        <button
          key={`${action.tick}-${action.verb}-${i}`}
          type="button"
          data-action-row={action.verb}
          data-action-ok={action.ok ? "true" : "false"}
          onClick={() => onSnapshotClick?.(action.tick)}
          className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-colors hover:border-[var(--color-primary)]/50"
        >
          <div className="flex items-start gap-3">
            <span className="mt-0.5 shrink-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text-muted)]">
              #{action.tick}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Zap className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                <span className="text-sm font-medium text-[var(--color-text)]">
                  {action.summary}
                </span>
                {!action.ok && (
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                    Failed
                  </span>
                )}
              </div>
              {!action.ok && action.error && (
                <p className="mt-1 break-words text-xs leading-relaxed text-amber-500/90">
                  {action.error}
                </p>
              )}
              <p className="mt-1 font-mono text-[10px] text-[var(--color-text-muted)]">
                {action.verb}
              </p>
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}

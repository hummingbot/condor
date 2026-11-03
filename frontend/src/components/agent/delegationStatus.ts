import type { DelegationStatus, DelegationSummary } from "@/lib/api";

// How a delegation's states read, owned in one place: the fleet report's task
// list, the chat workspace's context dock and the history view all colour them.
// Keyed exhaustively — an unmapped status would crash the row that renders it.
export const DELEGATION_STATUS: Record<
  DelegationStatus,
  { dot: string; text: string; label: string }
> = {
  running: {
    dot: "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]",
    text: "text-emerald-400",
    label: "RUNNING",
  },
  done: { dot: "bg-sky-400", text: "text-sky-400", label: "DONE" },
  error: { dot: "bg-red-400", text: "text-red-400", label: "ERROR" },
  stopped: {
    dot: "bg-[var(--color-text-muted)]/50",
    text: "text-[var(--color-text-muted)]",
    label: "STOPPED",
  },
  // The process died with the task still running: not an error the agent hit,
  // and not a clean stop either.
  interrupted: {
    dot: "bg-amber-400",
    text: "text-amber-400",
    label: "INTERRUPTED",
  },
  // A code run cut off by its own budget: not a snippet that raised, and not a
  // clean finish either — so it reads beside `interrupted`, its nearest
  // relative, because in both something outside the run ended it (FEAT-061).
  timeout: {
    dot: "bg-amber-400",
    text: "text-amber-400",
    label: "TIMEOUT",
  },
  // A transcript too old to say how it ended. Honest beats a guess.
  unknown: {
    dot: "bg-[var(--color-text-muted)]/30",
    text: "text-[var(--color-text-muted)]",
    label: "UNKNOWN",
  },
};

/**
 * How long this task has been going, compactly.
 *
 * Only a running task gets a live elapsed time; everything else reports when it
 * *started* rather than inventing a duration — the registry records no end, and
 * a history record's `ended_at` is about when it finished, not how long it took.
 */
export function formatDelegationTime(
  d: Pick<DelegationSummary, "started_at" | "status">,
  now = Date.now(),
): string {
  if (!d.started_at) return "";
  const secs = Math.max(0, now / 1000 - d.started_at);
  const compact =
    secs < 60
      ? `${Math.floor(secs)}s`
      : secs < 3600
        ? `${Math.floor(secs / 60)}m`
        : `${Math.floor(secs / 3600)}h`;
  return d.status === "running" ? compact : `${compact} ago`;
}

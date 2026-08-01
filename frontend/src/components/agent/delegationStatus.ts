import type { Delegation } from "@/lib/api";

// How a delegation's four states read, owned in one place: the fleet report's
// task list and the chat workspace's context dock both colour them.
export const DELEGATION_STATUS: Record<
  Delegation["status"],
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
};

/**
 * How long this task has been going, compactly.
 *
 * Only a running task gets a live elapsed time. A finished one has no end
 * timestamp — the registry never recorded one — so it reports when it *started*
 * rather than inventing a duration.
 */
export function formatDelegationTime(d: Delegation, now = Date.now()): string {
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

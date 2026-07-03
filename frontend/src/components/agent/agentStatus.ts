import type { RunningInstance } from "@/lib/api";

export const STATUS_STYLES: Record<string, { dot: string; bg: string; label: string }> = {
  running: { dot: "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]", bg: "border-emerald-500/30 bg-emerald-500/5", label: "LIVE" },
  dry_run: { dot: "bg-blue-400", bg: "border-blue-500/30 bg-blue-500/5", label: "DRY RUN" },
  paused: { dot: "bg-amber-400", bg: "border-amber-500/30 bg-amber-500/5", label: "PAUSED" },
  stopped: { dot: "bg-red-400/60", bg: "border-red-500/20 bg-red-500/5", label: "STOPPED" },
  idle: { dot: "bg-[var(--color-text-muted)]/40", bg: "border-[var(--color-border)] bg-[var(--color-surface)]", label: "IDLE" },
};

// An agent (or strategy) counts as "live" only when it's running a real loop.
// If its only running instance(s) are experiments (dry_run / run_once) —
// observation-only and transient — surface it as "dry_run" so it doesn't read
// as a green LIVE loop or inflate the Live Agents count. Mirrors the DRY RUN
// badge in the Active Sessions table. The raw `status` is "running" whenever
// any engine (incl. an experiment) is alive.
export function deriveAgentStatus(entity: { status: string; instances?: RunningInstance[] }): string {
  if (entity.status !== "running") return entity.status;
  const runningInstances = (entity.instances || []).filter((i) => i.status === "running");
  if (
    runningInstances.length > 0 &&
    runningInstances.every((i) => i.execution_mode === "dry_run" || i.execution_mode === "run_once")
  ) {
    return "dry_run";
  }
  return entity.status;
}

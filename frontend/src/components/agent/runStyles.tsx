/* eslint-disable react-refresh/only-export-components */
// Single source of truth for run-kind and run-status encodings (RunStore §7.1).

// Run kinds whose executors are attributed to the agent (they launch the tick
// engine and tag controller_id == run_id).
export const TRADING_KINDS = new Set(["session", "experiment", "scheduled"]);

export const RUN_KIND_STYLES: Record<string, { label: string; badge: string }> = {
  session: { label: "session", badge: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400" },
  experiment: { label: "exp", badge: "border-amber-500/30 bg-amber-500/10 text-amber-400" },
  delegation: { label: "delegation", badge: "border-purple-500/30 bg-purple-500/10 text-purple-400" },
  consult: { label: "consult", badge: "border-sky-500/30 bg-sky-500/10 text-sky-400" },
  scheduled: { label: "scheduled", badge: "border-blue-500/30 bg-blue-500/10 text-blue-400" },
};

const UNKNOWN_KIND = {
  label: "run",
  badge: "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]",
};

export function KindBadge({ kind }: { kind: string }) {
  const s = RUN_KIND_STYLES[kind] ?? UNKNOWN_KIND;
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase ${s.badge}`}>
      {s.label}
    </span>
  );
}

// RunStore run statuses: running | completed | stopped | error | interrupted | unknown.
export const RUN_STATUS_COLORS: Record<string, string> = {
  running: "text-emerald-400",
  completed: "text-sky-400",
  stopped: "text-[var(--color-text-muted)]",
  error: "text-red-400",
  interrupted: "text-amber-400",
  unknown: "text-[var(--color-text-muted)]",
};

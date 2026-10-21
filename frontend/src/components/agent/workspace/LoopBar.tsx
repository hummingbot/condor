import { ChevronDown } from "lucide-react";

import {
  formatDuration,
  isLiveRun,
  runDurationSec,
  runLabel,
} from "@/components/agent/lab/runs";
import {
  driftLines,
  dueInSec,
  effectiveModel,
  throttleLines,
  throttledVenues,
  tickCountdownLabel,
} from "@/components/agent/workspace/fleet";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type {
  AgentRunRow,
  RunningInstance,
  StrategySummary,
} from "@/lib/api";

/**
 * What the loop is doing, above whatever you are reading (FEAT-103).
 *
 * The second of the three regions that never unmount. Before this the loop's
 * state and the loop's output were on different pages: the workbench knew the
 * cadence and the countdown, the Lab knew the run and the ticks, and neither
 * knew the other — so reading a snapshot could not tell you whether the loop
 * that wrote it was still running, and watching the countdown could not tell
 * you what the last tick decided.
 *
 * The run's ticks (`TickSpine`) are not drawn here: they share one row with
 * the section tabs below this bar (ARCH-425), because each alone left most of
 * a full-width row empty. This bar keeps the pickers, the status, the cadence,
 * the countdown, and what the session is actually running — the model the
 * adapter reports and whether its config still matches the file (FEAT-136).
 *
 * The two pickers are native selects on purpose. They are the scope of
 * everything below them and they have to be operable at any width, and the
 * portalled menus this codebase builds for richer chips are for controls that
 * carry state a select cannot show.
 */
export function LoopBar({
  strategies,
  sslug,
  onSelectStrategy,
  runs,
  run,
  onSelectRun,
  instance,
}: {
  /** Every strategy this agent owns — the scope selector's options. */
  strategies: readonly StrategySummary[];
  sslug: string | null;
  onSelectStrategy: (sslug: string) => void;
  /** The runs of the strategy in scope, newest first. */
  runs: readonly AgentRunRow[];
  run: AgentRunRow | null;
  onSelectRun: (runId: string) => void;
  /** The live engine, when there is one — the cadence and the countdown. */
  instance: RunningInstance | null;
}) {
  const live = !!run && isLiveRun(run);
  // One clock for the bar, alive only while a run is. A `Date.now()` here would
  // be a render-phase read of a moving value, and on a bar that only re-renders
  // when the 5s query settles it would lurch rather than count.
  const now = useSeconds(live);
  const nowSec = now / 1000;

  const duration = run ? formatDuration(runDurationSec(run, nowSec)) : "";
  const dueIn = dueInSec(instance, nowSec);
  // What this session is actually running, beside what it was asked to run
  // (FEAT-136). Both read off the live engine, so they are only here while
  // there is one — a stopped run's drift is not a thing an operator can act on.
  const drift = driftLines(instance);
  const model = effectiveModel(instance);
  // Which venues are refusing this session (FEAT-138). A venue condition, so it
  // is worded as the venue's doing and sits apart from the run's own error —
  // before this a throttled host reached the operator as a slow tick.
  const throttled = throttledVenues(instance);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--color-border)] px-4 py-1.5 text-xs">
      {strategies.length > 0 && (
        <Picker
          label="Loop"
          value={sslug ?? ""}
          onChange={onSelectStrategy}
          options={strategies.map((s) => ({ value: s.slug, label: s.name }))}
        />
      )}

      {run && (
        <Picker
          label="Run"
          value={run.run_id}
          onChange={onSelectRun}
          options={runs.map((r) => ({
            value: r.run_id,
            label: `${runLabel(r)} · ${r.tick_count} tick${r.tick_count === 1 ? "" : "s"}`,
          }))}
        />
      )}

      {run && (
        <>
          <span
            className={
              live
                ? "text-emerald-400"
                : run.error
                  ? "text-[var(--color-red)]"
                  : "text-[var(--color-text-muted)]"
            }
          >
            {run.status}
          </span>
          {duration && (
            <>
              <span className="opacity-40">·</span>
              <span className="font-mono text-[var(--color-text-muted)]">
                {duration}
              </span>
            </>
          )}
        </>
      )}

      {instance && instance.frequency_sec > 0 && (
        <>
          <span className="opacity-40">·</span>
          <span className="font-mono text-[var(--color-text-muted)]">
            every {countdown(instance.frequency_sec)}
          </span>
        </>
      )}

      {dueIn !== null && (
        <>
          <span className="opacity-40">·</span>
          <span
            data-loop-countdown
            className={`font-mono ${
              dueIn > 0 ? "text-[var(--color-text-muted)]" : "text-amber-400"
            }`}
          >
            {tickCountdownLabel(dueIn)}
          </span>
        </>
      )}

      {model && (
        <>
          <span className="opacity-40">·</span>
          <span
            data-effective-model
            title={
              model.requested
                ? `Requested ${model.requested}; the adapter reports ${model.active}`
                : `The adapter reports ${model.active}`
            }
            className={`font-mono ${
              model.mismatch ? "text-amber-400" : "text-[var(--color-text-muted)]"
            }`}
          >
            {model.active}
            {model.mismatch ? ` (pinned ${model.requested})` : ""}
          </span>
        </>
      )}

      {throttled.length > 0 && (
        <>
          <span className="opacity-40">·</span>
          <span
            data-venue-throttled
            title={`These venues are refusing our calls — not a strategy fault:\n${throttleLines(throttled).join("\n")}`}
            className="rounded bg-amber-400/15 px-1.5 py-0.5 font-medium text-amber-400"
          >
            {throttled.length === 1
              ? `${throttled[0].venue} throttling (${throttled[0].refusals} refused)`
              : `${throttled.length} venues throttling`}
          </span>
        </>
      )}

      {drift.length > 0 && (
        <>
          <span className="opacity-40">·</span>
          <span
            data-config-drift
            title={`This session is still running its start-time config. Restart to apply:\n${drift.join("\n")}`}
            className="rounded bg-amber-400/15 px-1.5 py-0.5 font-medium text-amber-400"
          >
            config drifted ({drift.length})
          </span>
        </>
      )}
    </div>
  );
}

function Picker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-[var(--color-text-muted)]">
      <span className="sr-only">{label}</span>
      <span className="relative flex items-center">
        <select
          aria-label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="appearance-none rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 pl-2 pr-6 text-xs font-medium text-[var(--color-text)] transition-colors hover:border-[var(--color-primary)]/50"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-1.5 h-3 w-3 text-[var(--color-text-muted)]" />
      </span>
    </label>
  );
}

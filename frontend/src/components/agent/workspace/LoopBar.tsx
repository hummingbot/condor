import { ChevronDown } from "lucide-react";

import { TickSpine } from "@/components/agent/lab/TickSpine";
import {
  formatDuration,
  isLiveRun,
  runDurationSec,
  runLabel,
} from "@/components/agent/lab/runs";
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
 * `TickSpine` is promoted out of the Lab's body to sit here, which is a move
 * and not a rewrite: it already takes `selectedTick` / `onSelectTick` and
 * derives its beat colours from `lab/runs.ts`. Selecting a beat sets `?tick=`
 * and swaps the body; it never navigates, because a tick is a selection that
 * has to survive changing what you are looking at.
 *
 * The two pickers are native selects on purpose. They are the scope of
 * everything below them and they have to be operable at any width, and the
 * portalled menus this codebase builds for richer chips are for controls that
 * carry state a select cannot show.
 */
export function LoopBar({
  slug,
  strategies,
  sslug,
  onSelectStrategy,
  runs,
  run,
  onSelectRun,
  instance,
  tick,
  onSelectTick,
}: {
  slug: string;
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
  tick: number | null;
  onSelectTick: (tick: number | null) => void;
}) {
  const live = !!run && isLiveRun(run);
  // One clock for the bar, alive only while a run is. A `Date.now()` here would
  // be a render-phase read of a moving value, and on a bar that only re-renders
  // when the 5s query settles it would lurch rather than count.
  const now = useSeconds(live);
  const nowSec = now / 1000;

  const duration = run ? formatDuration(runDurationSec(run, nowSec)) : "";
  const dueIn =
    instance && instance.last_tick_at > 0
      ? instance.last_tick_at + instance.frequency_sec - nowSec
      : null;

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--color-border)] px-4 py-1.5 text-xs">
        {strategies.length > 0 && (
          <Picker
            label="Strategy"
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
              {dueIn > 0
                ? `next in ${countdown(dueIn)}`
                : `overdue ${countdown(-dueIn)}`}
            </span>
          </>
        )}
      </div>

      {/* The run's ticks, as navigation — the third region that stays put. A
          dry run is a single tick in one file and has no spine to draw. */}
      {run && run.kind === "session" && sslug && (
        <TickSpine
          slug={slug}
          sslug={sslug}
          sessionNum={run.number}
          hasActionsLog={run.has_actions_log}
          selectedTick={tick}
          onSelectTick={onSelectTick}
        />
      )}
    </>
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

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  BEAT_TITLES,
  actionsByTick,
  beatState,
  type BeatState,
} from "@/components/agent/lab/runs";
import { api } from "@/lib/api";
import { parseJournal } from "@/lib/parse-agent";

/**
 * The run's ticks, as its navigation.
 *
 * This is `LoopPulse`'s beat strip promoted from a header ornament to the spine
 * of the page: a tick number *is* a snapshot's name, so a row of beats is the
 * whole history of a run and the fastest way into any moment of it. `LoopPulse`
 * itself stays on the strategy workbench, where it reports the loop running
 * *now*; this one reports a run that may have ended weeks ago.
 *
 * The colour rule lives in `lab/runs.ts` so it is testable without a DOM. Its
 * fourth state is the one that matters: a run written before the action log
 * existed has no record of what any tick did, and is drawn grey with a tooltip
 * that says so — never hollow, which would claim every tick did nothing.
 */
const BEAT_CLASS: Record<BeatState, string> = {
  failed: "bg-[var(--color-red)]",
  ok: "bg-[var(--color-green)]",
  idle: "border border-[var(--color-text-muted)]/50 bg-transparent",
  unlogged: "bg-[var(--color-text-muted)]/30",
};

export function TickSpine({
  slug,
  sslug,
  sessionNum,
  hasActionsLog,
  selectedTick,
  onSelectTick,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  /** Whether this run keeps an `actions.jsonl` at all. From the runs index. */
  hasActionsLog: boolean;
  /** The tick in the URL, or `null` for the run overview. */
  selectedTick: number | null;
  onSelectTick: (tick: number | null) => void;
}) {
  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  // The same call `SessionActions` makes, argument for argument, so the two
  // share one cache entry rather than each paying for the log.
  const { data: actionsData } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  const ticks = useMemo(
    () => (journalData?.content ? parseJournal(journalData.content).ticks : []),
    [journalData?.content],
  );
  const byTick = useMemo(
    () => actionsByTick(actionsData?.actions ?? []),
    [actionsData?.actions],
  );

  if (ticks.length === 0) {
    return (
      <p
        data-spine-empty
        className="px-4 py-2 text-[11px] text-[var(--color-text-muted)]"
      >
        No ticks recorded for this run.
      </p>
    );
  }

  return (
    <div
      data-testid="tick-spine"
      className="flex flex-wrap items-center gap-1 border-b border-[var(--color-border)]/60 px-4 py-2"
    >
      <button
        type="button"
        data-spine-overview
        onClick={() => onSelectTick(null)}
        className={`mr-1 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-colors ${
          selectedTick === null
            ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
            : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        }`}
      >
        Run
      </button>
      {ticks.map((entry) => {
        const deeds = byTick.get(entry.tick) ?? [];
        const state = beatState({
          actions: deeds,
          journalActions: entry.actions,
          hasActionsLog,
        });
        // The deed is what the tick *did*; the journal line is what it said
        // about doing it. Prefer the deed, and fall back to the rule's own
        // sentence when there is neither.
        const title =
          deeds.map((d) => d.summary).join(" · ") ||
          entry.summary ||
          BEAT_TITLES[state];
        return (
          <button
            key={entry.tick}
            type="button"
            data-beat={entry.tick}
            data-beat-state={state}
            title={`#${entry.tick} — ${title}`}
            onClick={() => onSelectTick(entry.tick)}
            className={`h-5 w-2 rounded-sm transition-all hover:scale-y-110 ${BEAT_CLASS[state]} ${
              selectedTick === entry.tick
                ? "ring-2 ring-[var(--color-primary)] ring-offset-1 ring-offset-[var(--color-bg)]"
                : ""
            }`}
          />
        );
      })}
      {!hasActionsLog && (
        <span className="ml-2 text-[10px] text-[var(--color-text-muted)]/70">
          no action log for this run
        </span>
      )}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

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
 * A row of beats as the spine of the page: a tick number *is* a snapshot's
 * name, so a row of beats is the whole history of a run and the fastest way
 * into any moment of it — including a run that may have ended weeks ago.
 *
 * The colour rule lives in `lab/runs.ts` so it is testable without a DOM. Its
 * fourth state is the one that matters: a run written before the action log
 * existed has no record of what any tick did, and is drawn grey with a tooltip
 * that says so — never hollow, which would claim every tick did nothing.
 *
 * `bare` is how the run screen draws it (ARCH-425): one line beside the
 * section tabs rather than a row of its own, so it brings no border or padding
 * and never wraps — a long session scrolls sideways instead, and opens on its
 * newest beat, which is the one a reader came for.
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
  bare = false,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  /** Whether this run keeps an `actions.jsonl` at all. From the runs index. */
  hasActionsLog: boolean;
  /** The tick in the URL, or `null` for the run overview. */
  selectedTick: number | null;
  onSelectTick: (tick: number | null) => void;
  /** Embedded in a row that owns the border: one scrolling line, no chrome. */
  bare?: boolean;
}) {
  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  // The same call the Runs tab's ticks make, argument for argument, so the two
  // share one cache entry rather than each paying for the log.
  const { data: actionsData } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  // Hoisted rather than reached through: the compiler infers
  // `journalData` as the dependency and will not preserve a memo that declares
  // a narrower one.
  const journalContent = journalData?.content;
  const ticks = useMemo(
    () => (journalContent ? parseJournal(journalContent).ticks : []),
    [journalContent],
  );
  const byTick = useMemo(
    () => actionsByTick(actionsData?.actions ?? []),
    [actionsData?.actions],
  );

  // Opens on the newest beat, and follows new ones as a live run writes them —
  // but only while the reader is already at the end: someone who scrolled back
  // to an old tick is reading it, and a beat arriving is no reason to yank them.
  const stripRef = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  const beatCount = ticks.length;
  useEffect(() => {
    const el = stripRef.current;
    if (bare && el && atEnd.current) el.scrollLeft = el.scrollWidth;
  }, [bare, beatCount]);

  if (ticks.length === 0) {
    return (
      <p
        data-spine-empty
        className={`${bare ? "py-2" : "px-4 py-2"} text-[11px] text-[var(--color-text-muted)]`}
      >
        No ticks recorded for this run.
      </p>
    );
  }

  return (
    <div
      ref={stripRef}
      data-testid="tick-spine"
      onScroll={
        bare
          ? (e) => {
              const el = e.currentTarget;
              atEnd.current =
                el.scrollLeft + el.clientWidth >= el.scrollWidth - 4;
            }
          : undefined
      }
      className={`flex items-center gap-1 ${
        // The vertical padding is room for the selected beat's ring, which an
        // `overflow-x-auto` box would otherwise clip (its overflow-y goes auto
        // too); the horizontal is the same room for the first and last beat.
        bare
          ? "min-w-0 flex-nowrap overflow-x-auto px-1 py-2 [scrollbar-width:thin]"
          : "flex-wrap border-b border-[var(--color-border)]/60 px-4 py-2"
      }`}
    >
      <button
        type="button"
        data-spine-overview
        onClick={() => onSelectTick(null)}
        className={`mr-1 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-colors ${
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
            className={`h-5 w-2 shrink-0 rounded-sm transition-all hover:scale-y-110 ${BEAT_CLASS[state]} ${
              selectedTick === entry.tick
                ? "ring-2 ring-[var(--color-primary)] ring-offset-1 ring-offset-[var(--color-bg)]"
                : ""
            }`}
          />
        );
      })}
      {!hasActionsLog && (
        <span className="ml-2 shrink-0 whitespace-nowrap text-[10px] text-[var(--color-text-muted)]/70">
          no action log for this run
        </span>
      )}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  BEAT_TITLES,
  actionsByTick,
  beatState,
  type BeatState,
} from "@/components/agent/lab/runs";
import type { AgentActionRow } from "@/lib/agent-attribution";
import { api } from "@/lib/api";
import { parseJournal, type TickEntry } from "@/lib/parse-agent";

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
 *
 * Hovering (or focusing) a beat opens a card with what the tick did: its time,
 * its state, the journal's line and every deed with its error. The card is
 * portalled and fixed-positioned because the strip is a sideways scroller,
 * whose `overflow` would clip anything drawn inside it.
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

  // The beat under the pointer (or keyboard focus), with where it is on screen.
  const [hovered, setHovered] = useState<{ tick: number; rect: DOMRect } | null>(null);
  const showCard = (tick: number, el: HTMLElement) =>
    setHovered({ tick, rect: el.getBoundingClientRect() });
  const hideCard = () => setHovered(null);
  const hoveredEntry = hovered
    ? ticks.find((t) => t.tick === hovered.tick) ?? null
    : null;
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
              // The card is pinned to where the beat *was*; a scroll moves it.
              hideCard();
              const el = e.currentTarget;
              atEnd.current =
                el.scrollLeft + el.clientWidth >= el.scrollWidth - 4;
            }
          : undefined
      }
      className={`flex items-center ${
        // Each beat's button is taller than its bar, which is the room the
        // hovered or selected bar's ring needs — an `overflow-x-auto` box would
        // otherwise clip it (its overflow-y goes auto too).
        bare
          ? "min-w-0 flex-nowrap overflow-x-auto py-0.5 [scrollbar-width:thin]"
          : "flex-wrap border-b border-[var(--color-border)]/60 px-4 py-1"
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
        const isHovered = hovered?.tick === entry.tick;
        const isSelected = selectedTick === entry.tick;
        return (
          <button
            key={entry.tick}
            type="button"
            data-beat={entry.tick}
            data-beat-state={state}
            aria-label={`#${entry.tick} — ${title}`}
            onClick={() => onSelectTick(entry.tick)}
            onMouseEnter={(e) => showCard(entry.tick, e.currentTarget)}
            onMouseLeave={hideCard}
            onFocus={(e) => showCard(entry.tick, e.currentTarget)}
            onBlur={hideCard}
            data-beat-hovered={isHovered || undefined}
            data-beat-selected={isSelected || undefined}
            // The button is the target and the bar is drawn inside it: the
            // targets sit edge to edge (no dead gap to slip through) while the
            // bars keep room between them. Hovering grows only the bar and
            // gives it a contour — the button keeps its width, so nothing
            // reflows and the neighbours stay where the pointer expects them.
            className="group flex h-8 w-4 shrink-0 items-center justify-center"
          >
            <span
              aria-hidden
              className={`rounded-sm transition-all duration-100 ${BEAT_CLASS[state]} ${
                isHovered ? "h-6 w-3" : "h-5 w-2"
              } ${
                isSelected
                  ? "ring-2 ring-[var(--color-primary)] ring-offset-1 ring-offset-[var(--color-bg)]"
                  : isHovered
                    ? "ring-2 ring-[var(--color-text)] ring-offset-1 ring-offset-[var(--color-bg)]"
                    : ""
              }`}
            />
          </button>
        );
      })}
      {!hasActionsLog && (
        <span className="ml-2 shrink-0 whitespace-nowrap text-[10px] text-[var(--color-text-muted)]/70">
          no action log for this run
        </span>
      )}
      {hovered && hoveredEntry && (
        <BeatCard
          entry={hoveredEntry}
          deeds={byTick.get(hoveredEntry.tick) ?? []}
          state={beatState({
            actions: byTick.get(hoveredEntry.tick) ?? [],
            journalActions: hoveredEntry.actions,
            hasActionsLog,
          })}
          anchor={hovered.rect}
        />
      )}
    </div>
  );
}

const STATE_LABEL: Record<BeatState, string> = {
  failed: "Action failed",
  ok: "Actions ran",
  idle: "No actions",
  unlogged: "Not logged",
};

const STATE_TEXT: Record<BeatState, string> = {
  failed: "text-[var(--color-red)]",
  ok: "text-[var(--color-green)]",
  idle: "text-[var(--color-text-muted)]",
  unlogged: "text-[var(--color-text-muted)]",
};

const CARD_WIDTH = 320;
const CARD_GAP = 6;
const CARD_EDGE = 8;
/** Past this many deeds the card says how many more rather than growing. */
const MAX_DEEDS = 6;
/** Below this much room under the beat the card opens above it instead. */
const CARD_MIN_ROOM = 240;

/**
 * One beat's detail, over the page.
 *
 * `pointer-events-none` so it never takes the hover that keeps it up — the
 * beat under it stays the thing you click.
 */
function BeatCard({
  entry,
  deeds,
  state,
  anchor,
}: {
  entry: TickEntry;
  deeds: AgentActionRow[];
  state: BeatState;
  anchor: DOMRect;
}) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const left = Math.max(
    CARD_EDGE,
    Math.min(
      anchor.left + anchor.width / 2 - CARD_WIDTH / 2,
      vw - CARD_WIDTH - CARD_EDGE,
    ),
  );
  const above = vh - anchor.bottom < CARD_MIN_ROOM && anchor.top > vh - anchor.bottom;
  const position = above
    ? { left, bottom: vh - anchor.top + CARD_GAP }
    : { left, top: anchor.bottom + CARD_GAP };
  const shown = deeds.slice(0, MAX_DEEDS);

  return createPortal(
    <div
      role="tooltip"
      data-beat-card={entry.tick}
      style={{ position: "fixed", width: CARD_WIDTH, ...position }}
      className="pointer-events-none z-[60] space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text)] shadow-lg"
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-sm font-bold">#{entry.tick}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-[var(--color-text-muted)]">
          {entry.timestamp}
        </span>
        <span
          className={`shrink-0 text-[10px] font-bold uppercase tracking-wider ${STATE_TEXT[state]}`}
        >
          {STATE_LABEL[state]}
        </span>
      </div>

      {entry.summary ? (
        <p data-beat-card-summary className="line-clamp-4 leading-snug">
          {entry.summary}
        </p>
      ) : (
        <p className="text-[var(--color-text-muted)]">No summary written</p>
      )}

      {shown.length > 0 && (
        <ul className="space-y-1 border-t border-[var(--color-border)] pt-2">
          {shown.map((d, i) => (
            <li key={i} data-beat-card-deed className="flex gap-1.5 leading-snug">
              <span
                className={`shrink-0 font-bold ${
                  d.ok ? "text-[var(--color-green)]" : "text-[var(--color-red)]"
                }`}
              >
                {d.ok ? "✓" : "✗"}
              </span>
              <span className="min-w-0">
                <span className="line-clamp-2">{d.summary}</span>
                {!d.ok && d.error && (
                  <span className="line-clamp-2 text-[var(--color-red)]">{d.error}</span>
                )}
              </span>
            </li>
          ))}
          {deeds.length > shown.length && (
            <li className="text-[var(--color-text-muted)]">
              +{deeds.length - shown.length} more
            </li>
          )}
        </ul>
      )}

      {state === "unlogged" && (
        <p className="text-[10px] text-[var(--color-text-muted)]">{BEAT_TITLES.unlogged}</p>
      )}

      <p className="border-t border-[var(--color-border)] pt-1.5 text-[10px] text-[var(--color-text-muted)]">
        Click to open this tick
      </p>
    </div>,
    document.body,
  );
}

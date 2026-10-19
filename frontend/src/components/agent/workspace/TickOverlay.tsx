import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { adjacentTicks } from "@/components/agent/lab/runs";
import { SnapshotDetail } from "@/components/agent/session/Snapshot";
import { snapshotQueryOptions } from "@/hooks/useSnapshotBubbles";
import { api } from "@/lib/api";
import { parseJournal } from "@/lib/parse-agent";

/** True when a key press belongs to whatever has focus, not to the overlay. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * One tick of a session, over the screen — with the run's other ticks a key
 * press away.
 *
 * ← and → (or the header's arrows) step to the previous and next tick the
 * journal recorded, so reviewing a session is reading it forwards rather than
 * closing and re-opening beat after beat. The journal is the same
 * `["strategy", …, "journal"]` entry the spine reads, so this costs no fetch,
 * and both neighbours' snapshots are prefetched so a step lands rendered.
 */
export function TickOverlay({
  slug,
  sslug,
  sessionNum,
  tick,
  onSelectTick,
  onClose,
  className,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  tick: number;
  onSelectTick: (tick: number) => void;
  onClose: () => void;
  className: string;
}) {
  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });
  const journalContent = journalData?.content;
  const ticks = useMemo(
    () =>
      journalContent ? parseJournal(journalContent).ticks.map((t) => t.tick) : [],
    [journalContent],
  );
  const { prev, next, index } = adjacentTicks(ticks, tick);

  const queryClient = useQueryClient();
  useEffect(() => {
    for (const t of [prev, next]) {
      if (t !== null && t > 0) {
        void queryClient.prefetchQuery(snapshotQueryOptions(slug, sslug, sessionNum, t));
      }
    }
  }, [queryClient, slug, sslug, sessionNum, prev, next]);

  // Kept in a ref so the listener attaches once rather than on every step.
  const stepRef = useRef({ prev, next, onSelectTick });
  useEffect(() => {
    stepRef.current = { prev, next, onSelectTick };
  });
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (isTypingTarget(e.target)) return;
      const { prev: p, next: n, onSelectTick: go } = stepRef.current;
      const target = e.key === "ArrowLeft" ? p : e.key === "ArrowRight" ? n : null;
      if (target === null) return;
      e.preventDefault();
      go(target);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const stepButton =
    "rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:pointer-events-none disabled:opacity-30";

  return (
    <div data-tick-overlay className={className}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-2">
        <div className="flex min-w-0 items-center gap-1">
          <button
            type="button"
            data-tick-prev
            onClick={() => prev !== null && onSelectTick(prev)}
            disabled={prev === null}
            aria-label="Previous tick"
            title="Previous tick (←)"
            className={stepButton}
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            data-tick-next
            onClick={() => next !== null && onSelectTick(next)}
            disabled={next === null}
            aria-label="Next tick"
            title="Next tick (→)"
            className={stepButton}
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          <span className="ml-1 truncate text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Tick #{tick} · session {sessionNum}
          </span>
          {index >= 0 && (
            <span
              data-tick-position
              className="ml-2 shrink-0 font-mono text-[10px] text-[var(--color-text-muted)]"
            >
              {index + 1}/{ticks.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close tick"
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {/* Keyed by tick so each step opens at the top of its snapshot. */}
      <div key={tick} className="min-h-0 flex-1 overflow-y-auto p-4">
        <SnapshotDetail slug={slug} sslug={sslug} sessionNum={sessionNum} tick={tick} />
      </div>
    </div>
  );
}

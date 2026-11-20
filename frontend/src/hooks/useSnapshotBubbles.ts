import { useQueries } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import type { SnapshotBubble } from "@/components/charts/ExecutorChart";
import { type SnapshotSummary, api } from "@/lib/api";
import { parseSnapshot } from "@/lib/parse-agent";

/**
 * The one cache entry a snapshot body lives in.
 *
 * Both readers of a snapshot go through here so they share it: the chart-marker
 * previews below, and `SnapshotDetail` when the user clicks a tick. Two callers
 * on one key is what makes the click free — react-query serves it from cache
 * instead of re-downloading a body the previews already paid for. The `staleTime`
 * belongs to the options, not to either caller: an observer that mounts with a
 * shorter one would refetch on mount and undo the sharing.
 */
export function snapshotQueryOptions(slug: string, sslug: string, sessionNum: number, tick: number) {
  return {
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "snapshot", tick] as const,
    queryFn: () => api.getSnapshot(slug, sslug, sessionNum, tick),
    staleTime: 60000,
  };
}

/**
 * A snapshot title's timestamp, as unix seconds — 0 when it cannot be read.
 *
 * The engine stamps titles `YYYY-MM-DD HH:MM UTC` (`condor/agents/engine.py`),
 * which no `Date` constructor is obliged to accept and V8 rejects once the
 * space is swapped for a `T`. So the engine's form is read field by field; any
 * other string still gets the old best-effort parse.
 */
export function parseSnapshotTs(ts: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(?:UTC|Z)$/.exec(ts.trim());
  if (m) {
    const [, y, mo, d, h, mi, sec] = m;
    return Math.floor(Date.UTC(+y, +mo - 1, +d, +h, +mi, sec ? +sec : 0) / 1000);
  }
  const d = new Date(ts.replace(" ", "T"));
  if (isNaN(d.getTime())) return 0;
  return Math.floor(d.getTime() / 1000);
}

/** The only two fields a marker needs out of a snapshot body. */
interface SnapshotPreview {
  agentResponse: string;
  toolCallCount: number;
}

/**
 * Module-level so its identity is stable: react-query re-runs `select` when the
 * function changes, and re-parsing a snapshot (system prompt included) on every
 * render is exactly the work this hook exists to avoid.
 */
function toPreview(data: { content: string } | undefined): SnapshotPreview | null {
  if (!data?.content) return null;
  const parsed = parseSnapshot(data.content);
  return { agentResponse: parsed.agentResponse, toolCallCount: parsed.toolCalls.length };
}

/**
 * Chart markers for a session's snapshots, one query per tick — fetched on hover.
 *
 * A marker needs only the tick and its timestamp, both already in the summary
 * row, so it exists from the moment the tick is known. The body is wanted for
 * one thing, the hover preview, and downloading every tick's whole snapshot on
 * mount to have it ready cost a request per tick (megabytes for a long session)
 * for previews nobody had asked for. So a body is fetched when its marker is
 * first hovered (`loadPreview`), under the key `SnapshotDetail` reads: a tick
 * already opened is a cache hit here, and a tick hovered here makes the click
 * free. An un-hovered marker still shows a preview when the body happens to be
 * cached — a disabled query serves what the cache holds.
 *
 * `bubbles` always has one entry per summary, in the summaries' order, so
 * nothing appears, disappears or re-orders under the user as bodies arrive.
 * react-query runs the result through `replaceEqualDeep`, so the reference
 * stays stable while the contents do; `ExecutorChart` keys effects off it and
 * would otherwise reposition every bubble on every render.
 */
export function useSnapshotBubbles(
  slug: string,
  sslug: string,
  sessionNum: number,
  summaries: SnapshotSummary[],
): { bubbles: SnapshotBubble[]; loadPreview: (tick: number) => void } {
  // Keyed by session as well as tick: tick numbers repeat across sessions, and
  // a hover in one must not start a download in the next.
  const session = `${slug}/${sslug}/${sessionNum}`;
  const [wanted, setWanted] = useState<ReadonlySet<string>>(() => new Set());
  const loadPreview = useCallback(
    (tick: number) => {
      const id = `${session}/${tick}`;
      setWanted((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    },
    [session],
  );

  const combine = useCallback(
    (results: { data?: SnapshotPreview | null; isError: boolean }[]): SnapshotBubble[] =>
      summaries.map((snap, i) => {
        const preview = results[i]?.data;
        if (!preview) {
          return results[i]?.isError
            ? { tick: snap.tick, timestamp: snap.timestamp, previewFailed: true }
            : { tick: snap.tick, timestamp: snap.timestamp };
        }
        return {
          tick: snap.tick,
          timestamp: snap.timestamp,
          agentResponse: preview.agentResponse,
          toolCallCount: preview.toolCallCount,
        };
      }),
    [summaries],
  );

  const bubbles = useQueries({
    queries: summaries.map((snap) => ({
      ...snapshotQueryOptions(slug, sslug, sessionNum, snap.tick),
      select: toPreview,
      enabled: wanted.has(`${session}/${snap.tick}`),
    })),
    combine,
  });

  return { bubbles, loadPreview };
}

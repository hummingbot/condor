import { useQueries } from "@tanstack/react-query";
import { useCallback } from "react";

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
 * Chart markers for a session's snapshots, one query per tick.
 *
 * A single batched query used to fetch every body in one `Promise.all` under a
 * key that ended in the joined tick list. That key made three problems at once:
 * one new tick minted a new key, so the whole batch refetched while the previous
 * one stranded under a dead key; nothing rendered until the slowest body landed;
 * and none of it was shared with `SnapshotDetail`, which fetched the same body
 * again on click.
 *
 * One query per tick fixes all three. A tick already fetched stays cached under
 * its own key, so a session that gains a tick costs exactly one request. Each
 * marker fills in as its own body lands. And the key is the one `SnapshotDetail`
 * reads, so the click is a cache hit.
 *
 * The returned array always has one entry per summary, in the summaries' order —
 * a marker exists from the moment the tick is known and only gains its preview
 * later, so nothing appears, disappears or re-orders under the user as bodies
 * arrive. react-query runs the result through `replaceEqualDeep`, so the
 * reference stays stable while the contents do; `ExecutorChart` keys effects off
 * it and would otherwise reposition every bubble on every render.
 */
export function useSnapshotBubbles(
  slug: string,
  sslug: string,
  sessionNum: number,
  summaries: SnapshotSummary[],
): SnapshotBubble[] {
  const combine = useCallback(
    (results: { data?: SnapshotPreview | null }[]): SnapshotBubble[] =>
      summaries.map((snap, i) => {
        const preview = results[i]?.data;
        if (!preview) return { tick: snap.tick, timestamp: snap.timestamp };
        return {
          tick: snap.tick,
          timestamp: snap.timestamp,
          agentResponse: preview.agentResponse,
          toolCallCount: preview.toolCallCount,
        };
      }),
    [summaries],
  );

  return useQueries({
    queries: summaries.map((snap) => ({
      ...snapshotQueryOptions(slug, sslug, sessionNum, snap.tick),
      select: toPreview,
    })),
    combine,
  });
}

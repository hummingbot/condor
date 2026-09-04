// ── Refreshing a performance history the socket is already tailing ──

import type {
  ControllerPerformanceHistoryAllResponse,
  ControllerPerformanceSnapshot,
} from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { MAX_HISTORY_ROWS } from "@/lib/history-pagination";
import { samplingIntervalMs } from "@/lib/pnl-chart";

/**
 * How often a chart re-checks its history when nothing else prompts it.
 *
 * Both charts used to poll on their own clock — 120s for the fleet, 60s per
 * controller — because the poll *was* the update mechanism. It is not: the
 * `controller_perf` channel pushes the latest snapshot every 30s
 * (`_controller_perf_stream`) straight into these cache entries, so the poll
 * only ever re-delivered what had already arrived. Since CORR-237 that
 * re-delivery is not one request either — a full walk is up to ten sequential
 * ones — which is what turned a redundant poll into an expensive one.
 *
 * So the interval stops being the update path and becomes the safety net for
 * the cases the socket cannot cover: a channel that was never subscribed, a
 * broadcast dropped without the connection closing, a server whose sampler
 * back-filled older buckets. Ten minutes is short enough that any of those
 * self-heals well inside a coffee break and long enough that the periodic cost
 * is negligible — and what it now issues is a tail, not a re-download.
 *
 * The socket's own reconnect is handled separately and immediately, in
 * `shared-socket.ts`: a connection that dropped and came back is a *known* gap
 * and should not wait for this timer.
 */
export const HISTORY_REFETCH_MS = 600_000;

/**
 * Requests one tail refresh may issue before it gives up and re-walks.
 *
 * A tail covers the window between the newest cached snapshot and now, which
 * in the ordinary case — a socket that has been feeding this cache all along —
 * is a handful of rows and ends on the first short page. Two pages is
 * therefore already generous: it absorbs a fleet-sized gap of several hours at
 * the finest interval.
 *
 * What matters is the *ceiling*, not the number. A tail that needs more than
 * this is no longer a tail, and pretending otherwise is how an incremental
 * refresh quietly becomes a second full download stacked on top of the one it
 * was meant to replace. Past the ceiling the honest move is the expensive one:
 * throw the cached series away and walk it properly, which also re-anchors the
 * window if the cache turned out to be missing its *newest* end rather than
 * its oldest.
 */
export const TAIL_MAX_PAGES = 2;

/** The newest snapshot instant in a series, or undefined if there is none. */
export function newestSnapshotMs(
  snapshots: ControllerPerformanceSnapshot[] | undefined,
): number | undefined {
  let newest: number | undefined;
  for (const snap of snapshots ?? []) {
    const ms = Date.parse(snap.timestamp);
    if (Number.isNaN(ms)) continue;
    if (newest === undefined || ms > newest) newest = ms;
  }
  return newest;
}

/**
 * The `start_time` a tail refresh of `previous` should ask for, or undefined
 * when the cached series cannot be safely extended and must be re-walked.
 *
 * Two decisions live here.
 *
 * **When a tail is allowed at all.** Not when there is no cache and not when
 * there is nothing in it — there is no newest end to resume from, and a tail
 * onto nothing is a history with no beginning. And not when `outcome` is
 * `"error"`: `collectCursorPages` keeps the pages it got before a failure, so
 * that series is real data of an unknown extent, and the only thing that can
 * establish its extent is walking it again. A cap, by contrast, is not a
 * defect to repair: `truncated: "row-cap"`/`"page-cap"` means the history is
 * genuinely longer than one chart may hold, so a re-walk returns the very same
 * window at ten times the cost. Those are extended, and the amber badge CORR-237
 * put on them stays on.
 *
 * **Where it resumes.** One sampling bucket *before* the newest cached
 * instant, never at it. The cache is fed from two sources with different
 * clocks: the walk stores interval-bucketed rows, while the socket stores raw
 * 30s dumps whatever the cache's interval is (`mergeIntoMatchingQueries`). So
 * the newest cached timestamp is routinely mid-bucket, and resuming exactly
 * there asks the server for buckets strictly after a bucket nobody has stored
 * — one point missing, permanently, at the seam. Re-requesting the boundary
 * bucket costs a row and cannot duplicate anything, because the merge dedupes
 * on the same `bot:controller:timestamp` composite the socket does.
 *
 * The interval is therefore load-bearing rather than incidental: it sizes the
 * overlap, and it is also what makes the cache identity a resolution-specific
 * one (it is the last element of both query keys, PERF-238). A tail computed
 * at one interval and merged into a series sampled at another would interleave
 * two resolutions, so both readers derive it from the entry they are refreshing.
 */
export function tailResumeFrom(
  previous: ControllerPerformanceHistoryAllResponse | undefined,
  interval: string | undefined,
): string | undefined {
  if (!previous || previous.outcome === "error") return undefined;
  const newest = newestSnapshotMs(previous.snapshots);
  if (newest === undefined) return undefined;
  return new Date(newest - samplingIntervalMs(interval)).toISOString();
}

/**
 * Merge a tail response onto the cached series it extends.
 *
 * Deduplicated on `bot:controller:timestamp` — the same composite the walk and
 * the socket both key on, because `controller_id` is a config id two bots can
 * share and their rows land on one shared dump timestamp (CORR-241) — so the
 * deliberate overlap window, and anything the socket already delivered, folds
 * away instead of doubling a point.
 *
 * `maxRows` is what keeps a tab left open overnight from growing without
 * bound: tails append forever, and so does the socket, so a series that is
 * never replaced is a series that only ever gets longer. Past the budget the
 * oldest rows go, which is exactly the window a fresh walk of the same budget
 * would have returned — and because the series then no longer reaches back to
 * where it started, it is marked `truncated` so the chart says so. It stays
 * tailable: a row cap is a window, not damage (see `tailResumeFrom`).
 *
 * Everything else comes from `previous`, except the two fields that describe
 * the *server* rather than the series — `server_online` and `error_hint` are
 * the tail's, since they are only meaningful as of the last request made.
 */
export function mergeHistoryTail(
  previous: ControllerPerformanceHistoryAllResponse,
  tail: ControllerPerformanceHistoryAllResponse,
  maxRows: number = MAX_HISTORY_ROWS,
): ControllerPerformanceHistoryAllResponse {
  const snapKey = (s: ControllerPerformanceSnapshot) => `${controllerKey(s)}:${s.timestamp}`;
  const seen = new Set<string>();
  const merged: ControllerPerformanceSnapshot[] = [];
  for (const snap of [...(previous.snapshots ?? []), ...(tail.snapshots ?? [])]) {
    const key = snapKey(snap);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(snap);
  }

  let snapshots = merged;
  let truncated = previous.truncated;
  let outcome = previous.outcome;
  if (merged.length > maxRows) {
    // Newest wins. The array is unordered by contract — the socket appends and
    // the fold sorts — so the trim has to sort rather than slice off an end.
    snapshots = [...merged]
      .sort((a, b) => (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0))
      .slice(0, maxRows);
    truncated = true;
    outcome = "row-cap";
  }

  return {
    ...previous,
    snapshots,
    truncated,
    outcome,
    pages: tail.pages,
    server_online: tail.server_online,
    error_hint: tail.error_hint,
  };
}

/**
 * One refresh of a controller-performance history query.
 *
 * The whole point of the item this implements (PERF-239): the periodic refresh
 * used to re-issue the same unbounded request the first load did — the entire
 * history from bot start, every 120s, and since CORR-237 as up to ten
 * sequential requests — while the `controller_perf` channel was already
 * pushing that same tail into the very cache entry about to be overwritten.
 *
 * So a refresh asks only for what the cache does not already have, and merges
 * rather than replaces. The full walk is not deleted, it is demoted: it runs on
 * the first load, and afterwards only when the cache cannot be extended — no
 * entry, an empty one, a walk that failed partway, or a tail that could not
 * finish inside its page ceiling.
 *
 * That last fallback is what makes the whole thing safe without depending on
 * which end of a capped series is the missing one. If a truncated cache turns
 * out to be missing its *newest* end rather than its oldest, the tail window is
 * enormous, the walk hits `TAIL_MAX_PAGES`, and this falls straight through to
 * the full walk it would have done anyway. The cost of guessing wrong is two
 * requests, and it self-corrects on the same refresh rather than leaving a
 * chart wrong until someone reloads.
 *
 * `full` and `tail` are passed in rather than called here so the policy can be
 * tested without a network, and so the two readers keep their own row budgets
 * and their own query parameters.
 */
export async function refreshControllerHistory(opts: {
  previous: ControllerPerformanceHistoryAllResponse | undefined;
  interval: string | undefined;
  full: () => Promise<ControllerPerformanceHistoryAllResponse>;
  tail: (startTime: string) => Promise<ControllerPerformanceHistoryAllResponse>;
  maxRows?: number;
}): Promise<ControllerPerformanceHistoryAllResponse> {
  const from = tailResumeFrom(opts.previous, opts.interval);
  if (from === undefined) return opts.full();

  const tail = await opts.tail(from);
  // A tail that ran out of pages is not a tail; the cached series may be
  // missing more than its right-hand edge, so re-establish it properly.
  if (tail.outcome !== "complete") return opts.full();

  return mergeHistoryTail(opts.previous!, tail, opts.maxRows);
}

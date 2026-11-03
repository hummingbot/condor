// ── One bounded cursor walk, for the dashboard's paginated reads ──

import { HISTORY_POINT_BUDGET } from "@/lib/pnl-chart";

/**
 * Rows a single history request may ask for.
 *
 * This is the route's ceiling, not a preference: `limit` is declared
 * `Query(1000, ge=1, le=1000)` (CORR-260), so 1000 is simultaneously the
 * default and the largest page that exists. Asking for more is a 422, not a
 * bigger page — which is precisely why the fix for a short chart is another
 * request, not a larger one.
 */
export const HISTORY_PAGE_SIZE = 1000;

/**
 * The most pages one walk will issue, and the rows that implies.
 *
 * The cap is a budget, not a termination condition — the four signals in
 * `collectCursorPages` are what normally end a walk. It exists so that a bot
 * with a year of history, a server that keeps handing back cursors, or a fleet
 * nobody has pruned cannot turn one chart mount into an unbounded sequence of
 * sequential round-trips. Ten pages is roughly a second of network on a healthy
 * server and a bounded few MB of JSON; past that the honest answer is a shorter
 * series that *says* it is shorter.
 */
export const MAX_HISTORY_PAGES = 10;
export const MAX_HISTORY_ROWS = HISTORY_PAGE_SIZE * MAX_HISTORY_PAGES;

/**
 * How many rows a chart over `controllerCount` controllers should ask for.
 *
 * The bug this exists for is that `limit` counts ROWS while a chart is drawn
 * from TIMESTAMPS, and the two differ by a factor nobody chose: the sampler
 * writes one row per controller per dump under a shared timestamp, so a
 * thousand rows is a thousand instants for one controller and a hundred for
 * ten. `pickSamplingInterval` already sized the *interval* so that the span
 * fits `HISTORY_POINT_BUDGET` instants (PERF-238); this sizes the *row* budget
 * to match, by multiplying by the number of series that will each contribute a
 * row at each of those instants.
 *
 * The ratio is not exactly N. Sampled coarsely the endpoint tends toward one
 * row per bucket in total, with controllers appearing round-robin, so N is an
 * upper bound and this budget usually ends the walk early on a short page — the
 * cheap outcome. The floor of two pages matters for the opposite reason: a
 * single-controller chart whose ratio is exactly 1 would otherwise be given
 * exactly one page and could never discover that a boundary bucket pushed it
 * one row over.
 */
export function historyRowBudget(
  controllerCount: number,
  pointBudget: number = HISTORY_POINT_BUDGET,
): number {
  const wanted = Math.max(1, Math.floor(controllerCount || 0)) * pointBudget;
  return Math.min(MAX_HISTORY_ROWS, Math.max(2 * HISTORY_PAGE_SIZE, wanted));
}

/** One page of a cursor-paginated response, normalised. */
export interface CursorPage<T> {
  rows: T[];
  /** The cursor to send for the following page, or null when this was the last. */
  nextCursor: string | null;
}

/** Why a walk stopped. Only `"complete"` means the history genuinely ran out. */
export type WalkOutcome = "complete" | "row-cap" | "page-cap" | "error";

export interface CursorWalk<T> {
  rows: T[];
  /** How many requests were actually issued. */
  pages: number;
  /** True unless the history ran out on its own — the series is missing its oldest end. */
  truncated: boolean;
  outcome: WalkOutcome;
  /** Where a resumed walk would continue; null when the history ran out. */
  nextCursor: string | null;
  /** The failure that ended a partial walk, when `outcome` is `"error"`. */
  error?: unknown;
}

/**
 * Walk a cursor-paginated endpoint to exhaustion, or to a cap, and return
 * everything it handed back.
 *
 * This is the browser-side twin of `condor/fetchers/_pagination.py`, which the
 * Telegram path has walked for a long time while the dashboard issued exactly
 * one request and dropped `next_cursor` on the floor (CORR-237). The four
 * terminal conditions are the same four, and they are the same four because
 * each of them has been the one that mattered at least once:
 *
 *  - **empty page** — nothing came back, so nothing more will.
 *  - **no cursor** — the server says this was the last page.
 *  - **short page** — fewer rows than we asked for says the same thing, for a
 *    server that echoes a cursor unconditionally.
 *  - **non-advancing cursor** — the server handed back the cursor we sent it.
 *    Following it re-requests the identical page forever; without this guard the
 *    walk runs to its cap appending duplicate rows, which is silent corruption
 *    rather than a visible hang.
 *
 * On top of those, three things a chart in a browser has to survive that a
 * fetcher in a bot does not:
 *
 *  - **the caps.** `maxRows`/`maxPages` bound a genuinely enormous history. The
 *    result then says `truncated: true`, and the caller is expected to say so on
 *    screen: a series missing its oldest end that draws like a complete one is
 *    the bug this whole item is about, and re-introducing it at a different
 *    cause would be no better.
 *  - **a failure partway.** The first page failing means there is nothing to
 *    draw, so it throws and react-query does its usual retry. A *later* page
 *    failing is different: pages 1..n-1 are real data, and discarding them to
 *    show an empty chart serves nobody. So the walk keeps what it has, marks it
 *    truncated, and returns — a flaky server degrades to a shorter chart with a
 *    warning rather than to no chart at all.
 *  - **the user leaving.** `signal` is react-query's, so switching bots or
 *    unmounting the page aborts the in-flight request and stops the walk at the
 *    next boundary; the abort propagates as a rejection, which is exactly what
 *    react-query expects from a cancelled query and what stops a stale walk from
 *    being written into a cache entry nobody is looking at.
 *
 * `dedupeKey` drops a row already seen. Pages that overlap by a row are normal
 * (the newest page is being appended to while it is read), and while the chart
 * fold happens to be idempotent over a repeated snapshot, nothing says the next
 * consumer will be.
 */
export async function collectCursorPages<T>(
  fetchPage: (req: { limit: number; cursor?: string }) => Promise<CursorPage<T>>,
  opts: {
    pageSize?: number;
    maxRows?: number;
    maxPages?: number;
    signal?: AbortSignal;
    dedupeKey?: (row: T) => string;
  } = {},
): Promise<CursorWalk<T>> {
  const pageSize = opts.pageSize ?? HISTORY_PAGE_SIZE;
  const maxRows = opts.maxRows ?? MAX_HISTORY_ROWS;
  const maxPages = opts.maxPages ?? MAX_HISTORY_PAGES;

  const rows: T[] = [];
  const seen = opts.dedupeKey ? new Set<string>() : null;
  let fetched = 0;
  let pages = 0;
  let cursor: string | undefined;

  const done = (outcome: WalkOutcome, nextCursor: string | null, error?: unknown): CursorWalk<T> => ({
    rows,
    pages,
    truncated: outcome !== "complete",
    outcome,
    nextCursor,
    ...(error === undefined ? {} : { error }),
  });

  for (;;) {
    throwIfAborted(opts.signal);

    // Never fetch rows the cap would make us discard.
    const remaining = maxRows - fetched;
    if (remaining <= 0) return done("row-cap", cursor ?? null);
    if (pages >= maxPages) return done("page-cap", cursor ?? null);

    const limit = Math.min(pageSize, remaining);
    let page: CursorPage<T>;
    try {
      page = await fetchPage(cursor ? { limit, cursor } : { limit });
    } catch (err) {
      // Nothing to show, or the user left: let the caller see the failure.
      if (pages === 0 || isAbort(err)) throw err;
      return done("error", cursor ?? null, err);
    }

    pages += 1;
    fetched += page.rows.length;
    for (const row of page.rows) {
      if (seen) {
        const key = opts.dedupeKey!(row);
        if (seen.has(key)) continue;
        seen.add(key);
      }
      rows.push(row);
    }

    const following = page.nextCursor || null;
    if (page.rows.length === 0) return done("complete", null);
    if (!following || page.rows.length < limit) return done("complete", null);
    if (following === cursor) return done("complete", null);
    cursor = following;
  }
}

/** `signal.throwIfAborted()`, without depending on the environment having it. */
function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  throw signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

/** Is this the rejection an aborted fetch produces, rather than a server failure? */
function isAbort(err: unknown): boolean {
  return !!err && typeof err === "object" && (err as { name?: string }).name === "AbortError";
}

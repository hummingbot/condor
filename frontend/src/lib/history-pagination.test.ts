/**
 * The bounded cursor walk, pinned (CORR-237).
 *
 * Every one of these cases is a way a chart can silently draw a lie: a page
 * dropped or reordered is a hole in the middle of the line, a walk that never
 * terminates is a hung tab, a walk that terminates at a cap without saying so is
 * the original bug wearing a different hat, and a failure on page three that
 * throws away pages one and two is an empty chart where a partial one was
 * available.
 */

import { describe, expect, it, vi } from "vitest";

import {
  HISTORY_PAGE_SIZE,
  MAX_HISTORY_PAGES,
  MAX_HISTORY_ROWS,
  collectCursorPages,
  historyRowBudget,
} from "./history-pagination";

/** A row that carries where it came from, so ordering is checkable. */
interface Row {
  id: string;
}

/**
 * A fake endpoint over a scripted list of pages.
 *
 * `pages[i]` is answered for the i-th request; the cursor it hands back is
 * simply `"c<i+1>"`, and requests are recorded so tests can assert on what was
 * actually asked for.
 */
function scripted(pages: { rows: Row[]; nextCursor: string | null }[]) {
  const calls: { limit: number; cursor?: string }[] = [];
  const fetchPage = vi.fn(async (req: { limit: number; cursor?: string }) => {
    calls.push(req);
    const page = pages[calls.length - 1];
    if (!page) throw new Error(`no scripted page ${calls.length}`);
    return page;
  });
  return { fetchPage, calls };
}

/** `n` rows tagged with `prefix`, filling a page of `size`. */
function rows(prefix: string, n: number): Row[] {
  return Array.from({ length: n }, (_, i) => ({ id: `${prefix}-${i}` }));
}

describe("collectCursorPages", () => {
  it("concatenates every page, in the order the server sent them", async () => {
    const { fetchPage, calls } = scripted([
      { rows: rows("p1", 3), nextCursor: "c1" },
      { rows: rows("p2", 3), nextCursor: "c2" },
      { rows: rows("p3", 1), nextCursor: null },
    ]);

    const walk = await collectCursorPages(fetchPage, { pageSize: 3 });

    expect(walk.pages).toBe(3);
    expect(walk.rows.map((r) => r.id)).toEqual([
      "p1-0", "p1-1", "p1-2",
      "p2-0", "p2-1", "p2-2",
      "p3-0",
    ]);
    // The first request carries no cursor at all — several endpoints reject an
    // explicit null — and each following one carries the previous answer's.
    expect(calls).toEqual([
      { limit: 3 },
      { limit: 3, cursor: "c1" },
      { limit: 3, cursor: "c2" },
    ]);
    expect(walk.truncated).toBe(false);
    expect(walk.outcome).toBe("complete");
    expect(walk.nextCursor).toBeNull();
  });

  it("stops when the server sends no cursor", async () => {
    const { fetchPage } = scripted([{ rows: rows("p1", 2), nextCursor: null }]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 2 });
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(walk.outcome).toBe("complete");
    expect(walk.truncated).toBe(false);
  });

  it("treats an absent cursor the same as a null one", async () => {
    const { fetchPage } = scripted([
      { rows: rows("p1", 2), nextCursor: undefined as unknown as null },
    ]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 2 });
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(walk.truncated).toBe(false);
  });

  it("stops on a short page even when a cursor is still offered", async () => {
    const { fetchPage } = scripted([{ rows: rows("p1", 1), nextCursor: "c1" }]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 5 });
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(walk.outcome).toBe("complete");
  });

  it("stops on an empty page", async () => {
    const { fetchPage } = scripted([{ rows: [], nextCursor: "c1" }]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 5 });
    expect(walk.rows).toEqual([]);
    expect(walk.pages).toBe(1);
    expect(walk.outcome).toBe("complete");
  });

  it("stops when the server hands back the cursor it was sent", async () => {
    // Without this guard the walk re-requests the identical page until the cap,
    // appending duplicates — silent corruption rather than a visible hang.
    const { fetchPage } = scripted([
      { rows: rows("p1", 2), nextCursor: "stuck" },
      { rows: rows("p2", 2), nextCursor: "stuck" },
      { rows: rows("p3", 2), nextCursor: "stuck" },
    ]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 2 });
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(walk.rows).toHaveLength(4);
    expect(walk.outcome).toBe("complete");
  });

  it("stops at the row cap and says the result is truncated", async () => {
    const { fetchPage, calls } = scripted([
      { rows: rows("p1", 2), nextCursor: "c1" },
      { rows: rows("p2", 2), nextCursor: "c2" },
      { rows: rows("p3", 1), nextCursor: "c3" },
    ]);

    const walk = await collectCursorPages(fetchPage, { pageSize: 2, maxRows: 5 });

    expect(walk.rows).toHaveLength(5);
    expect(walk.truncated).toBe(true);
    expect(walk.outcome).toBe("row-cap");
    // Where a resumed walk would pick up, rather than a pretend "that was all":
    // the last page's own cursor, since that page was answered in full.
    expect(walk.nextCursor).toBe("c3");
    // The last page is clamped to what is left of the cap, so no row is fetched
    // only to be discarded.
    expect(calls[2]).toEqual({ limit: 1, cursor: "c2" });
  });

  it("stops at the page cap and says the result is truncated", async () => {
    const { fetchPage } = scripted(
      Array.from({ length: 5 }, (_, i) => ({ rows: rows(`p${i}`, 2), nextCursor: `c${i}` })),
    );
    const walk = await collectCursorPages(fetchPage, { pageSize: 2, maxPages: 3, maxRows: 1000 });
    expect(fetchPage).toHaveBeenCalledTimes(3);
    expect(walk.rows).toHaveLength(6);
    expect(walk.outcome).toBe("page-cap");
    expect(walk.truncated).toBe(true);
    expect(walk.nextCursor).toBe("c2");
  });

  it("keeps the pages it already has when a later page fails", async () => {
    const boom = new Error("gateway timeout");
    const fetchPage = vi
      .fn<(req: { limit: number; cursor?: string }) => Promise<{ rows: Row[]; nextCursor: string | null }>>()
      .mockResolvedValueOnce({ rows: rows("p1", 2), nextCursor: "c1" })
      .mockResolvedValueOnce({ rows: rows("p2", 2), nextCursor: "c2" })
      .mockRejectedValueOnce(boom);

    const walk = await collectCursorPages(fetchPage, { pageSize: 2 });

    // A slow or flaky server degrades to a shorter chart, not to no chart.
    expect(walk.rows.map((r) => r.id)).toEqual(["p1-0", "p1-1", "p2-0", "p2-1"]);
    expect(walk.pages).toBe(2);
    expect(walk.outcome).toBe("error");
    expect(walk.truncated).toBe(true);
    expect(walk.error).toBe(boom);
    expect(walk.nextCursor).toBe("c2");
  });

  it("throws when the very first page fails", async () => {
    const boom = new Error("offline");
    const fetchPage = vi.fn().mockRejectedValue(boom);
    // Nothing to draw and nothing to salvage: react-query must see the failure
    // so it can retry and show its error state.
    await expect(collectCursorPages(fetchPage, { pageSize: 2 })).rejects.toBe(boom);
  });

  it("propagates an abort even when pages have already landed", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    const fetchPage = vi
      .fn<(req: { limit: number; cursor?: string }) => Promise<{ rows: Row[]; nextCursor: string | null }>>()
      .mockResolvedValueOnce({ rows: rows("p1", 2), nextCursor: "c1" })
      .mockRejectedValueOnce(abort);

    // The user switched bots mid-walk. A half series must not be cached for a
    // key nobody is looking at, so the rejection is passed straight through.
    await expect(collectCursorPages(fetchPage, { pageSize: 2 })).rejects.toBe(abort);
  });

  it("stops at the next page boundary once the signal is aborted", async () => {
    const controller = new AbortController();
    const fetchPage = vi.fn(async () => {
      controller.abort();
      return { rows: rows("p1", 2), nextCursor: "c1" };
    });

    await expect(
      collectCursorPages(fetchPage, { pageSize: 2, signal: controller.signal }),
    ).rejects.toBeTruthy();
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it("drops a row that appeared on two pages", async () => {
    // Pages overlap when the newest one is being appended to as it is read.
    const { fetchPage } = scripted([
      { rows: [{ id: "a" }, { id: "b" }], nextCursor: "c1" },
      { rows: [{ id: "b" }, { id: "c" }], nextCursor: null },
    ]);
    const walk = await collectCursorPages(fetchPage, { pageSize: 2, dedupeKey: (r) => r.id });
    expect(walk.rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
});

describe("historyRowBudget", () => {
  it("scales with the fleet, because a row is per controller and a point is not", async () => {
    expect(historyRowBudget(3, 1000)).toBe(3000);
    expect(historyRowBudget(5, 1000)).toBe(5000);
  });

  it("gives even a single controller room for a second page", () => {
    // Otherwise a one-row overshoot at a bucket boundary is undiscoverable: the
    // walk would be handed exactly one page and could never look past it.
    expect(historyRowBudget(1, 1000)).toBe(2 * HISTORY_PAGE_SIZE);
    expect(historyRowBudget(0, 1000)).toBe(2 * HISTORY_PAGE_SIZE);
  });

  it("never exceeds the hard ceiling, however large the fleet", () => {
    expect(historyRowBudget(50, 1000)).toBe(MAX_HISTORY_ROWS);
    expect(MAX_HISTORY_ROWS).toBe(HISTORY_PAGE_SIZE * MAX_HISTORY_PAGES);
  });

  it("asks for pages no larger than the route's ceiling", () => {
    // `limit` is Query(1000, ge=1, le=1000) upstream (CORR-260): a larger value
    // is a 422, not a bigger page.
    expect(HISTORY_PAGE_SIZE).toBe(1000);
  });
});

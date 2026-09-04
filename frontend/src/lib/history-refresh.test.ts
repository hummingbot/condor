/**
 * When a performance-history refresh may extend the cache, and when it has to
 * start over (PERF-239).
 *
 * The wire-level half of this — the query string a routine refresh actually
 * issues, and the fact that it is *one* request rather than a re-walk — is
 * pinned in `components/bots/ControllerPnlChart.refresh.test.tsx` against the
 * real component. This file pins the policy underneath it, which is where the
 * ways to be wrong live: extending a series that has no established beginning,
 * losing the bucket at the seam between two sources with different clocks, and
 * letting a tab left open all day grow a cache without end.
 */

import { describe, expect, it } from "vitest";

import type {
  ControllerPerformanceHistoryAllResponse,
  ControllerPerformanceSnapshot,
} from "./api";
import {
  mergeHistoryTail,
  newestSnapshotMs,
  refreshControllerHistory,
  tailResumeFrom,
} from "./history-refresh";

const MINUTE = 60_000;

function snap(over: Partial<ControllerPerformanceSnapshot> = {}): ControllerPerformanceSnapshot {
  return {
    timestamp: "2026-08-27T00:00:00.000Z",
    bot_name: "bot-a",
    controller_id: "ctrl-1",
    controller_name: "pmm",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
    ...over,
  };
}

/** A cached series, complete unless said otherwise. */
function cached(
  snapshots: ControllerPerformanceSnapshot[],
  over: Partial<ControllerPerformanceHistoryAllResponse> = {},
): ControllerPerformanceHistoryAllResponse {
  return {
    snapshots,
    next_cursor: null,
    interval: "5m",
    pages: 3,
    truncated: false,
    outcome: "complete",
    ...over,
  };
}

/** `n` snapshots one minute apart ending at `endIso`. */
function series(endIso: string, n: number, controllerId = "ctrl-1"): ControllerPerformanceSnapshot[] {
  const end = Date.parse(endIso);
  return Array.from({ length: n }, (_, i) =>
    snap({ controller_id: controllerId, timestamp: new Date(end - (n - 1 - i) * MINUTE).toISOString() }),
  );
}

describe("newestSnapshotMs", () => {
  it("is the maximum instant regardless of array order", () => {
    const snaps = [
      snap({ timestamp: "2026-08-27T00:10:00.000Z" }),
      snap({ timestamp: "2026-08-27T00:30:00.000Z" }),
      snap({ timestamp: "2026-08-27T00:20:00.000Z" }),
    ];
    expect(newestSnapshotMs(snaps)).toBe(Date.parse("2026-08-27T00:30:00.000Z"));
  });

  it("ignores unparseable timestamps rather than poisoning the maximum", () => {
    expect(newestSnapshotMs([snap({ timestamp: "not a date" }), snap()])).toBe(
      Date.parse("2026-08-27T00:00:00.000Z"),
    );
  });

  it("is undefined for an empty series", () => {
    expect(newestSnapshotMs([])).toBeUndefined();
    expect(newestSnapshotMs(undefined)).toBeUndefined();
  });
});

describe("tailResumeFrom", () => {
  it("resumes one sampling bucket before the newest cached instant", () => {
    // The walk stores interval buckets; the socket stores raw 30s dumps into
    // the same entry. Resuming exactly at the newest cached instant would ask
    // for buckets strictly after a bucket nobody stored.
    const from = tailResumeFrom(cached(series("2026-08-27T06:00:00.000Z", 5)), "5m");
    expect(from).toBe("2026-08-27T05:55:00.000Z");
  });

  it("sizes that overlap from the interval, so two resolutions back off differently", () => {
    // The interval is the last element of both query keys (PERF-238), so it is
    // part of the cache identity a refresh resumes; it has to be part of the
    // resume arithmetic too, or a coarse series loses its seam bucket.
    const entry = cached(series("2026-08-27T06:00:00.000Z", 5), { interval: "1h" });
    expect(tailResumeFrom(entry, "1h")).toBe("2026-08-27T05:00:00.000Z");
    expect(tailResumeFrom(entry, "1d")).toBe("2026-08-26T06:00:00.000Z");
  });

  it("falls back to the finest bucket for an interval the route did not echo back", () => {
    const entry = cached(series("2026-08-27T06:00:00.000Z", 1));
    expect(tailResumeFrom(entry, "7m")).toBe("2026-08-27T05:55:00.000Z");
    expect(tailResumeFrom(entry, undefined)).toBe("2026-08-27T05:55:00.000Z");
  });

  it("spans the whole gap when the socket has been down for an hour", () => {
    const from = tailResumeFrom(cached(series("2026-08-27T05:00:00.000Z", 3)), "5m");
    // An hour later, the resume point is still the hour-old edge: the window a
    // tail asks for is the length of the gap, not of the history.
    expect(Date.parse(from!)).toBe(Date.parse("2026-08-27T04:55:00.000Z"));
  });

  it("refuses a tail when there is no cache to extend", () => {
    expect(tailResumeFrom(undefined, "5m")).toBeUndefined();
    expect(tailResumeFrom(cached([]), "5m")).toBeUndefined();
  });

  it("refuses a tail onto a walk that failed partway", () => {
    // `collectCursorPages` keeps the pages it got before a failure, so this is
    // real data of unknown extent — appending to it fixes the right-hand edge
    // and freezes the missing beginning in place forever.
    const entry = cached(series("2026-08-27T06:00:00.000Z", 3), {
      truncated: true,
      outcome: "error",
    });
    expect(tailResumeFrom(entry, "5m")).toBeUndefined();
  });

  it("still tails a series the row cap truncated", () => {
    // A cap is a window, not damage: re-walking returns the identical window at
    // ten times the cost, so the cheap path stays correct here.
    const entry = cached(series("2026-08-27T06:00:00.000Z", 3), {
      truncated: true,
      outcome: "row-cap",
    });
    expect(tailResumeFrom(entry, "5m")).toBe("2026-08-27T05:55:00.000Z");
  });
});

describe("mergeHistoryTail", () => {
  it("appends only what the cache did not already hold", () => {
    const previous = cached(series("2026-08-27T06:00:00.000Z", 3));
    const overlap = previous.snapshots[2];
    const tail = cached([overlap, snap({ timestamp: "2026-08-27T06:01:00.000Z" })], { pages: 1 });

    const merged = mergeHistoryTail(previous, tail);

    expect(merged.snapshots).toHaveLength(4);
    expect(new Set(merged.snapshots.map((s) => s.timestamp)).size).toBe(4);
  });

  it("dedupes on bot and controller, not on the controller id alone", () => {
    // Two bots running one controller config dump at a shared timestamp
    // (CORR-241); collapsing them would delete a whole series' point.
    const shared = "2026-08-27T06:00:00.000Z";
    const previous = cached([snap({ bot_name: "bot-a", timestamp: shared })]);
    const tail = cached([snap({ bot_name: "bot-b", timestamp: shared })], { pages: 1 });

    expect(mergeHistoryTail(previous, tail).snapshots).toHaveLength(2);
  });

  it("keeps the newest rows and says so when a long-open tab outgrows the budget", () => {
    const previous = cached(series("2026-08-27T06:00:00.000Z", 5));
    const tail = cached([snap({ timestamp: "2026-08-27T06:01:00.000Z" })], { pages: 1 });

    const merged = mergeHistoryTail(previous, tail, 3);

    expect(merged.snapshots.map((s) => s.timestamp)).toEqual([
      "2026-08-27T06:01:00.000Z",
      "2026-08-27T06:00:00.000Z",
      "2026-08-27T05:59:00.000Z",
    ]);
    // The series no longer reaches back to where it started, so the chart's
    // "partial history" badge (CORR-237) has to come on.
    expect(merged.truncated).toBe(true);
    expect(merged.outcome).toBe("row-cap");
  });

  it("leaves a series that fits inside the budget marked complete", () => {
    const previous = cached(series("2026-08-27T06:00:00.000Z", 2));
    const tail = cached([snap({ timestamp: "2026-08-27T06:01:00.000Z" })], { pages: 1 });

    const merged = mergeHistoryTail(previous, tail, 10);
    expect(merged.truncated).toBe(false);
    expect(merged.outcome).toBe("complete");
  });

  it("reports the requests this refresh made and the server's current state", () => {
    const previous = cached(series("2026-08-27T06:00:00.000Z", 2), { pages: 7 });
    const tail = cached([], { pages: 1, server_online: false, error_hint: "Connection error" });

    const merged = mergeHistoryTail(previous, tail);
    expect(merged.pages).toBe(1);
    expect(merged.server_online).toBe(false);
    expect(merged.error_hint).toBe("Connection error");
  });
});

describe("refreshControllerHistory", () => {
  /** Records which of the two paths ran. */
  function paths(tailResult?: ControllerPerformanceHistoryAllResponse) {
    const calls: string[] = [];
    return {
      calls,
      full: async () => {
        calls.push("full");
        return cached(series("2026-08-27T06:00:00.000Z", 4), { pages: 8 });
      },
      tail: async (from: string) => {
        calls.push(`tail:${from}`);
        return tailResult ?? cached([snap({ timestamp: "2026-08-27T06:01:00.000Z" })], { pages: 1 });
      },
    };
  }

  it("walks the whole history on the first load", async () => {
    const p = paths();
    await refreshControllerHistory({ previous: undefined, interval: "5m", ...p });
    expect(p.calls).toEqual(["full"]);
  });

  it("tails, and only tails, on a routine refresh", async () => {
    const p = paths();
    const previous = cached(series("2026-08-27T06:00:00.000Z", 4));

    const out = await refreshControllerHistory({ previous, interval: "5m", ...p });

    expect(p.calls).toEqual(["tail:2026-08-27T05:55:00.000Z"]);
    expect(out.snapshots).toHaveLength(5);
    expect(out.pages).toBe(1);
  });

  it("re-walks a truncated cache instead of appending to it", async () => {
    const p = paths();
    const previous = cached(series("2026-08-27T06:00:00.000Z", 4), {
      truncated: true,
      outcome: "error",
    });

    const out = await refreshControllerHistory({ previous, interval: "5m", ...p });

    expect(p.calls).toEqual(["full"]);
    expect(out.outcome).toBe("complete");
    expect(out.truncated).toBe(false);
  });

  it("gives up on a tail that ran out of pages and walks properly instead", async () => {
    // The safety net that makes the policy correct without assuming which end
    // of a capped series is the missing one: if the cache turns out to be short
    // at its *newest* end, the tail window is enormous, the walk hits its page
    // ceiling, and this falls through on the same refresh.
    const p = paths(cached([], { pages: 2, truncated: true, outcome: "page-cap" }));
    const previous = cached(series("2026-08-27T06:00:00.000Z", 4));

    const out = await refreshControllerHistory({ previous, interval: "5m", ...p });

    expect(p.calls).toEqual(["tail:2026-08-27T05:55:00.000Z", "full"]);
    expect(out.pages).toBe(8);
  });

  it("bounds the merged series with the caller's own row budget", async () => {
    const p = paths();
    const previous = cached(series("2026-08-27T06:00:00.000Z", 4));

    const out = await refreshControllerHistory({ previous, interval: "5m", maxRows: 2, ...p });

    expect(out.snapshots).toHaveLength(2);
    expect(out.truncated).toBe(true);
  });
});

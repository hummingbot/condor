/**
 * @vitest-environment jsdom
 *
 * The history walk as the charts actually issue it (CORR-237).
 *
 * `collectCursorPages` is tested on its own in history-pagination.test.ts; this
 * file pins the layer above it — the query string that goes on the wire, the
 * envelope that comes back, and above all the invariant that every page of one
 * series is fetched at one sampling interval (PERF-238). Splicing an hourly page
 * onto a five-minutely one produces a chart that is wrong in a way nothing
 * throws on and nobody can see, so it is asserted here against the real URLs
 * rather than against a mocked helper.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ControllerPerformanceSnapshot } from "./api";
import { HISTORY_PAGE_SIZE } from "./history-pagination";

const SERVER = "prod";

/** A snapshot with the fields the walk keys on; the rest is chart fodder. */
function snap(over: Partial<ControllerPerformanceSnapshot>): ControllerPerformanceSnapshot {
  return {
    timestamp: "2026-08-27T00:00:00Z",
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

/** `n` snapshots one minute apart, so a page is a distinguishable block. */
function page(prefix: string, n: number, botName = "bot-a", fromMinute = 0) {
  return Array.from({ length: n }, (_, i) =>
    snap({
      bot_name: botName,
      // `fromMinute` matters for a second page of the *same* controller: the
      // walk dedupes on `controller + timestamp`, so a page repeating the
      // previous one's instants is correctly discarded as overlap and would
      // make a cursor test look like a dropped page.
      timestamp: `2026-08-27T00:${String(fromMinute + i).padStart(2, "0")}:00Z`,
      controller_id: prefix,
    }),
  );
}

/** Serve a scripted list of responses and record every URL requested. */
function serve(bodies: Record<string, unknown>[]) {
  const urls: string[] = [];
  const fetchMock = vi.fn(async (url: string) => {
    urls.push(url);
    const body = bodies[urls.length - 1];
    if (!body) throw new Error(`no scripted response ${urls.length} (${url})`);
    return { ok: true, json: async () => body } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { urls, fetchMock };
}

/** The parsed query of the n-th request. */
function query(urls: string[], n: number): URLSearchParams {
  return new URL(urls[n], "http://localhost").searchParams;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getControllerPerformanceHistoryAll", () => {
  it("walks the cursor and assembles the pages into one series", async () => {
    const { urls } = serve([
      { snapshots: page("a", 3), next_cursor: "cur-1", interval: "1h" },
      { snapshots: page("b", 3), next_cursor: "cur-2", interval: "1h" },
      { snapshots: page("c", 2), next_cursor: null, interval: "1h" },
    ]);

    const res = await api.getControllerPerformanceHistoryAll(
      SERVER,
      { interval: "1h", start_time: "2026-07-01T00:00:00Z" },
      { pageSize: 3, maxRows: 100 },
    );

    expect(res.pages).toBe(3);
    expect(res.snapshots).toHaveLength(8);
    expect(res.snapshots.map((s) => s.controller_id)).toEqual([
      "a", "a", "a", "b", "b", "b", "c", "c",
    ]);
    expect(res.truncated).toBe(false);
    expect(res.outcome).toBe("complete");
    expect(res.next_cursor).toBeNull();

    // The first request carries no cursor; each following one carries the
    // previous response's.
    expect(query(urls, 0).get("cursor")).toBeNull();
    expect(query(urls, 1).get("cursor")).toBe("cur-1");
    expect(query(urls, 2).get("cursor")).toBe("cur-2");
  });

  it("sends the same interval — and the same window — on every page", async () => {
    const { urls } = serve([
      { snapshots: page("a", 2), next_cursor: "cur-1", interval: "4h" },
      { snapshots: page("b", 2), next_cursor: "cur-2", interval: "4h" },
      { snapshots: page("c", 2), next_cursor: null, interval: "4h" },
    ]);

    const res = await api.getControllerPerformanceHistoryAll(
      SERVER,
      { interval: "4h", start_time: "2026-01-01T00:00:00Z", bot_name: "bot-a" },
      { pageSize: 2, maxRows: 100 },
    );

    expect(urls).toHaveLength(3);
    for (let i = 0; i < urls.length; i++) {
      const q = query(urls, i);
      // The whole point: a follow-up page at a different resolution would splice
      // two resolutions into one line.
      expect(q.get("interval")).toBe("4h");
      expect(q.get("start_time")).toBe("2026-01-01T00:00:00Z");
      expect(q.get("bot_name")).toBe("bot-a");
      expect(q.get("limit")).toBe("2");
    }
    // And the interval the caller gets back describes the series it got.
    expect(res.interval).toBe("4h");
  });

  it("never asks for more rows than the route's ceiling", async () => {
    // `limit` is Query(1000, ge=1, le=1000) upstream (CORR-260) — a larger value
    // is a 422 the route reports as an offline server, not a bigger page.
    const { urls } = serve([{ snapshots: [], next_cursor: null, interval: "5m" }]);
    await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" });
    expect(Number(query(urls, 0).get("limit"))).toBeLessThanOrEqual(1000);
    expect(Number(query(urls, 0).get("limit"))).toBe(HISTORY_PAGE_SIZE);
  });

  it("stops when the server stops sending a cursor", async () => {
    const { fetchMock } = serve([{ snapshots: page("a", 5), next_cursor: null, interval: "5m" }]);
    const res = await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" }, { pageSize: 5 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.truncated).toBe(false);
  });

  it("marks a capped series truncated and keeps the cursor it stopped at", async () => {
    const { fetchMock } = serve([
      { snapshots: page("a", 2), next_cursor: "cur-1", interval: "5m" },
      { snapshots: page("b", 2), next_cursor: "cur-2", interval: "5m" },
    ]);

    const res = await api.getControllerPerformanceHistoryAll(
      SERVER,
      { interval: "5m" },
      { pageSize: 2, maxRows: 4 },
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(res.snapshots).toHaveLength(4);
    expect(res.truncated).toBe(true);
    expect(res.outcome).toBe("row-cap");
    expect(res.next_cursor).toBe("cur-2");
  });

  it("keeps the earlier pages when a later request fails", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (urls.length === 1) {
          return {
            ok: true,
            json: async () => ({ snapshots: page("a", 2), next_cursor: "cur-1", interval: "5m" }),
          } as unknown as Response;
        }
        return { ok: false, json: async () => ({ detail: "upstream timeout" }) } as unknown as Response;
      }),
    );

    const res = await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" }, { pageSize: 2 });

    expect(res.snapshots).toHaveLength(2);
    expect(res.outcome).toBe("error");
    expect(res.truncated).toBe(true);
  });

  it("lets a first-page failure through, since there is nothing to draw", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, json: async () => ({ detail: "offline" }) }) as unknown as Response),
    );
    await expect(api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" })).rejects.toThrow("offline");
  });

  it("does not double-count a snapshot that appears on two pages", async () => {
    // The newest page is being appended to while it is read, so an overlap of
    // one row between consecutive pages is normal.
    const shared = snap({ timestamp: "2026-08-27T00:05:00Z", controller_id: "ctrl-1" });
    serve([
      { snapshots: [snap({ timestamp: "2026-08-27T00:00:00Z" }), shared], next_cursor: "cur-1", interval: "5m" },
      { snapshots: [shared, snap({ timestamp: "2026-08-27T00:10:00Z" })], next_cursor: null, interval: "5m" },
    ]);

    const res = await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" }, { pageSize: 2 });

    expect(res.snapshots).toHaveLength(3);
    expect(res.snapshots.map((s) => s.timestamp)).toEqual([
      "2026-08-27T00:00:00Z",
      "2026-08-27T00:05:00Z",
      "2026-08-27T00:10:00Z",
    ]);
  });

  it("keeps both bots' rows when they share a controller id and a timestamp", async () => {
    // The dedupe key is the composite bot:controller (CORR-241); on the bare id
    // one of these two rows would vanish every dump.
    serve([
      {
        snapshots: [
          snap({ bot_name: "bot-a", controller_id: "ctrl-1", timestamp: "2026-08-27T00:00:00Z" }),
          snap({ bot_name: "bot-b", controller_id: "ctrl-1", timestamp: "2026-08-27T00:00:00Z" }),
        ],
        next_cursor: null,
        interval: "5m",
      },
    ]);

    const res = await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" }, { pageSize: 5 });
    expect(res.snapshots).toHaveLength(2);
    expect(res.snapshots.map((s) => s.bot_name)).toEqual(["bot-a", "bot-b"]);
  });

  it("passes an offline server's envelope through instead of walking it", async () => {
    const { fetchMock } = serve([
      { snapshots: [], next_cursor: null, interval: "5m", server_online: false, error_hint: "Connection error" },
    ]);
    const res = await api.getControllerPerformanceHistoryAll(SERVER, { interval: "5m" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.server_online).toBe(false);
    expect(res.error_hint).toBe("Connection error");
    expect(res.snapshots).toEqual([]);
  });
});

/**
 * The fleet's history is fetched one controller at a time (FEAT-089).
 *
 * The regression these guard is invisible on screen and always has been:
 * upstream's downsampler buckets by *time only*, so a request spanning several
 * controllers keeps one row per bucket and drops the rest — 12 of 12
 * controllers at `5m`, 11 of 12 at `1h`, and `samplingIntervalSince` picks
 * `15m` or coarser for any fleet older than ~3.5 days. The chart then
 * forward-filled whatever it received, so the line looked plausible while being
 * short of trading nobody could see was gone.
 *
 * Every assertion below about *which* requests go out is really an assertion
 * that each bucket contains one controller's rows and therefore loses nothing.
 */
describe("getControllerPerformanceHistoryByController", () => {
  const FLEET = [
    { bot_name: "bot-a", controller_id: "ctrl-1" },
    { bot_name: "bot-a", controller_id: "ctrl-2" },
    { bot_name: "bot-b", controller_id: "ctrl-1" },
  ];

  it("asks for each controller separately, so a coarse interval loses none", async () => {
    const { urls } = serve([
      { snapshots: page("ctrl-1", 2), next_cursor: null, interval: "1h" },
      { snapshots: page("ctrl-2", 2), next_cursor: null, interval: "1h" },
      { snapshots: page("ctrl-1", 2, "bot-b"), next_cursor: null, interval: "1h" },
    ]);

    const res = await api.getControllerPerformanceHistoryByController(
      SERVER,
      FLEET,
      { interval: "1h", start_time: "2026-07-01T00:00:00Z" },
      { pageSize: 2, maxRows: 100 },
    );

    expect(urls).toHaveLength(3);
    expect(res.snapshots).toHaveLength(6);
    for (let i = 0; i < 3; i++) {
      expect(query(urls, i).get("controller_id")).toBe(FLEET[i].controller_id);
      // Bound too, because a controller id is a *config* id two bots can
      // share (CORR-241) — an unbound one would merge them.
      expect(query(urls, i).get("bot_name")).toBe(FLEET[i].bot_name);
    }
  });

  it("asks every controller at the one interval it was given", async () => {
    const { urls } = serve([
      { snapshots: page("ctrl-1", 1), next_cursor: null, interval: "4h" },
      { snapshots: page("ctrl-2", 1), next_cursor: null, interval: "4h" },
      { snapshots: page("ctrl-1", 1, "bot-b"), next_cursor: null, interval: "4h" },
    ]);

    const res = await api.getControllerPerformanceHistoryByController(
      SERVER, FLEET, { interval: "4h" }, { pageSize: 1, maxRows: 100 },
    );

    expect(urls.map((_, i) => query(urls, i).get("interval"))).toEqual(["4h", "4h", "4h"]);
    expect(res.interval).toBe("4h");
  });

  it("walks each controller's own cursor to the end", async () => {
    serve([
      { snapshots: page("ctrl-1", 2), next_cursor: "c1", interval: "1h" },
      { snapshots: page("ctrl-1", 1, "bot-a", 2), next_cursor: null, interval: "1h" },
      { snapshots: page("ctrl-2", 1), next_cursor: null, interval: "1h" },
      { snapshots: page("ctrl-1", 1, "bot-b"), next_cursor: null, interval: "1h" },
    ]);

    const res = await api.getControllerPerformanceHistoryByController(
      SERVER, FLEET, { interval: "1h" }, { pageSize: 2, maxRows: 100, concurrency: 1 },
    );

    expect(res.pages).toBe(4);
    expect(res.snapshots).toHaveLength(5);
  });

  // A fleet series missing one controller's oldest end is a partial fleet
  // series, and the header says so — so one short walk has to be enough to
  // set it.
  it("is truncated when any one controller's walk was cut short", async () => {
    const { urls } = serve([
      { snapshots: page("ctrl-1", 2), next_cursor: "more", interval: "1h" },
      { snapshots: page("ctrl-2", 1), next_cursor: null, interval: "1h" },
      { snapshots: page("ctrl-1", 1, "bot-b"), next_cursor: null, interval: "1h" },
    ]);

    const res = await api.getControllerPerformanceHistoryByController(
      SERVER, FLEET, { interval: "1h" }, { pageSize: 2, maxRows: 2, concurrency: 1 },
    );

    expect(urls).toHaveLength(3);
    expect(res.truncated).toBe(true);
    expect(res.outcome).not.toBe("complete");
  });

  it("reports no cursor, because nothing resumes a fan-out by cursor", async () => {
    serve([
      { snapshots: page("ctrl-1", 1), next_cursor: "a", interval: "1h" },
      { snapshots: page("ctrl-2", 1), next_cursor: "b", interval: "1h" },
      { snapshots: page("ctrl-1", 1, "bot-b"), next_cursor: "c", interval: "1h" },
    ]);

    const res = await api.getControllerPerformanceHistoryByController(
      SERVER, FLEET, { interval: "1h" }, { pageSize: 1, maxRows: 1 },
    );
    expect(res.next_cursor).toBeNull();
  });

  it("issues nothing at all for a fleet with no controllers", async () => {
    const { urls } = serve([]);
    const res = await api.getControllerPerformanceHistoryByController(SERVER, [], {
      interval: "1h",
    });
    expect(urls).toHaveLength(0);
    expect(res.snapshots).toEqual([]);
    expect(res.truncated).toBe(false);
  });

  it("skips a controller with no id rather than asking for the whole fleet", async () => {
    const { urls } = serve([{ snapshots: page("ctrl-1", 1), next_cursor: null, interval: "1h" }]);
    await api.getControllerPerformanceHistoryByController(
      SERVER,
      [{ bot_name: "bot-a", controller_id: "" }, { bot_name: "bot-a", controller_id: "ctrl-1" }],
      { interval: "1h" },
      { pageSize: 1, maxRows: 10 },
    );
    expect(urls).toHaveLength(1);
    expect(query(urls, 0).get("controller_id")).toBe("ctrl-1");
  });
});

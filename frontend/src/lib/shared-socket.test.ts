/**
 * @vitest-environment jsdom
 *
 * `controller_perf` frames must land in the caches components actually read
 * (CORR-224).
 *
 * Both readers append a time bound the socket cannot know — the fleet query
 * adds `earliestDeploy`, the per-controller chart adds `deployedAt` — and
 * `setQueryData` matches the key hash exactly. Writing the bare prefix was a
 * silent no-op against an entry that never exists, so every broadcast was
 * discarded and the sparklines fell back entirely to their 120s/60s polls.
 */

import { beforeEach, describe, expect, it } from "vitest";

import type { ControllerPerformanceSnapshot } from "./api";
import { executorsQuery, queryClient } from "./queryClient";
import { handleMessage } from "./shared-socket";

const SERVER = "prod";

function snapshot(
  controllerId: string,
  timestamp: string,
  pnl = 1,
  botName = "epsilon",
): ControllerPerformanceSnapshot {
  return {
    timestamp,
    bot_name: botName,
    controller_id: controllerId,
    controller_name: controllerId,
    connector: "binance",
    trading_pair: "BTC-USDT",
    realized_pnl_quote: pnl,
    unrealized_pnl_quote: 0,
    global_pnl_quote: pnl,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
  };
}

function history(snapshots: ControllerPerformanceSnapshot[]) {
  return { snapshots, next_cursor: null, interval: "5m" };
}

function snapshotsAt(key: unknown[]): ControllerPerformanceSnapshot[] | undefined {
  return queryClient.getQueryData<{ snapshots: ControllerPerformanceSnapshot[] }>(key)
    ?.snapshots;
}

describe("controller_perf cache writes", () => {
  beforeEach(() => {
    queryClient.clear();
  });

  it("extends the fleet history keyed by earliestDeploy", () => {
    // ActiveBotsTab.tsx: ["controller-perf-history-all", server, earliestDeploy]
    const key = ["controller-perf-history-all", SERVER, "2026-08-01T00:00:00.000Z"];
    queryClient.setQueryData(key, history([snapshot("pmm_1", "100")]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200", 5)],
    });

    expect(snapshotsAt(key)?.map((s) => s.timestamp)).toEqual(["100", "200"]);
  });

  it("extends a per-controller history keyed by deployedAt", () => {
    // ControllerPnlChart.tsx: ["controller-perf-history", server, botName, controllerId, deployedAt]
    const key = ["controller-perf-history", SERVER, "epsilon", "pmm_1", "2026-08-01T00:00:00.000Z"];
    queryClient.setQueryData(key, history([snapshot("pmm_1", "100")]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200", 5), snapshot("pmm_2", "200", 9)],
    });

    const merged = snapshotsAt(key);
    expect(merged?.map((s) => s.timestamp)).toEqual(["100", "200"]);
    // Only this controller's snapshot, not the sibling's
    expect(merged?.every((s) => s.controller_id === "pmm_1")).toBe(true);
  });

  it("deduplicates by bot + controller + timestamp on a repeated frame", () => {
    const key = ["controller-perf-history-all", SERVER, undefined];
    queryClient.setQueryData(key, history([snapshot("pmm_1", "100")]));

    const frame = { snapshots: [snapshot("pmm_1", "200", 5)] };
    handleMessage(`controller_perf:${SERVER}`, frame);
    handleMessage(`controller_perf:${SERVER}`, frame);

    expect(snapshotsAt(key)).toHaveLength(2);
  });

  it("does not mint orphan entries for caches nobody has fetched", () => {
    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200")],
    });

    expect(
      queryClient
        .getQueryCache()
        .findAll({ queryKey: ["controller-perf-history-all"] }),
    ).toHaveLength(0);
    expect(
      queryClient.getQueryCache().findAll({ queryKey: ["controller-perf-history"] }),
    ).toHaveLength(0);
  });

  it("keeps the fleet and per-controller caches apart", () => {
    const fleet = ["controller-perf-history-all", SERVER, "d0"];
    const single = ["controller-perf-history", SERVER, "epsilon", "pmm_2", "d0"];
    queryClient.setQueryData(fleet, history([]));
    queryClient.setQueryData(single, history([]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200"), snapshot("pmm_2", "200")],
    });

    expect(snapshotsAt(fleet)).toHaveLength(2);
    expect(snapshotsAt(single)?.map((s) => s.controller_id)).toEqual(["pmm_2"]);
  });

  /**
   * Two bots running one controller config dump at the same instant, and the
   * frame carries both rows. Deduping on `controller_id:timestamp` treated the
   * second as a repeat of the first and dropped it, so one of the two bots
   * simply stopped receiving live points (CORR-241).
   */
  it("keeps both bots' snapshots at a shared controller id and timestamp", () => {
    const key = ["controller-perf-history-all", SERVER, "d0"];
    queryClient.setQueryData(key, history([]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [
        snapshot("grid_sol", "200", 5, "alpha"),
        snapshot("grid_sol", "200", 9, "beta"),
      ],
    });

    const merged = snapshotsAt(key);
    expect(merged).toHaveLength(2);
    expect(merged?.map((s) => s.bot_name)).toEqual(["alpha", "beta"]);
    // Still one row per bot when the very same frame arrives again.
    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [
        snapshot("grid_sol", "200", 5, "alpha"),
        snapshot("grid_sol", "200", 9, "beta"),
      ],
    });
    expect(snapshotsAt(key)).toHaveLength(2);
  });

  it("routes a per-controller history to its own bot, not its namesake's", () => {
    const alpha = ["controller-perf-history", SERVER, "alpha", "grid_sol", "d0"];
    const beta = ["controller-perf-history", SERVER, "beta", "grid_sol", "d0"];
    queryClient.setQueryData(alpha, history([]));
    queryClient.setQueryData(beta, history([]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [
        snapshot("grid_sol", "200", 5, "alpha"),
        snapshot("grid_sol", "200", 9, "beta"),
      ],
    });

    expect(snapshotsAt(alpha)?.map((s) => s.global_pnl_quote)).toEqual([5]);
    expect(snapshotsAt(beta)?.map((s) => s.global_pnl_quote)).toEqual([9]);
  });

  it("ignores frames for a different server", () => {
    const key = ["controller-perf-history-all", SERVER, "d0"];
    queryClient.setQueryData(key, history([]));

    handleMessage("controller_perf:staging", {
      snapshots: [snapshot("pmm_1", "200")],
    });

    expect(snapshotsAt(key)).toHaveLength(0);
  });
});

/**
 * An `executors` frame must reach the filtered views too (ARCH-227).
 *
 * Which narrowings someone is watching is recorded nowhere but in the live
 * query keys, so this bridge reads the filters back out of the cache. It used
 * to do that by index, and a key of an unexpected arity fell through every
 * branch in silence — the screen simply stopped updating. These cases hold the
 * factory and the parser to the same shape.
 */
describe("executors cache writes", () => {
  const BTC = { id: "e1", controller_id: "main", trading_pair: "BTC-USDT" };
  const SOL = { id: "e2", controller_id: "main", trading_pair: "SOL-USDC" };
  const OTHER = { id: "e3", controller_id: "grid_1", trading_pair: "BTC-USDT" };

  const ids = (key: unknown[]) =>
    queryClient.getQueryData<{ id: string }[]>(key)?.map((e) => e.id);

  beforeEach(() => {
    queryClient.clear();
  });

  it("writes the whole frame to the unfiltered entry", () => {
    handleMessage(`executors:${SERVER}`, [BTC, SOL, OTHER]);

    expect(ids(executorsQuery(SERVER).queryKey)).toEqual(["e1", "e2", "e3"]);
  });

  it("narrows the frame into a controller+pair entry", () => {
    // useMainControllerData: the pair-filtered trade panel list.
    const key = executorsQuery(SERVER, { controllerId: "main", pair: "BTC-USDT" })
      .queryKey;
    queryClient.setQueryData(key, []);

    handleMessage(`executors:${SERVER}`, [BTC, SOL, OTHER]);

    expect(ids(key)).toEqual(["e1"]);
  });

  it("narrows into single-filter entries as well", () => {
    const byPair = executorsQuery(SERVER, { pair: "SOL-USDC" }).queryKey;
    const byController = executorsQuery(SERVER, { controllerId: "grid_1" }).queryKey;
    queryClient.setQueryData(byPair, []);
    queryClient.setQueryData(byController, []);

    handleMessage(`executors:${SERVER}`, [BTC, SOL, OTHER]);

    expect(ids(byPair)).toEqual(["e2"]);
    expect(ids(byController)).toEqual(["e3"]);
  });

  it("keeps the unfiltered entry unfiltered", () => {
    const filtered = executorsQuery(SERVER, { pair: "BTC-USDT" }).queryKey;
    queryClient.setQueryData(filtered, []);

    handleMessage(`executors:${SERVER}`, [BTC, SOL]);

    expect(ids(executorsQuery(SERVER).queryKey)).toEqual(["e1", "e2"]);
    expect(ids(filtered)).toEqual(["e1"]);
  });

  it("does not mint filtered entries nobody is watching", () => {
    handleMessage(`executors:${SERVER}`, [BTC, SOL]);

    // Only the unfiltered entry the handler writes outright.
    expect(
      queryClient.getQueryCache().findAll({ queryKey: executorsQuery(SERVER).prefix }),
    ).toHaveLength(1);
  });

  it("leaves another server's entries alone", () => {
    const other = executorsQuery("staging", { pair: "BTC-USDT" }).queryKey;
    queryClient.setQueryData(other, [OTHER]);

    handleMessage(`executors:${SERVER}`, [BTC, SOL]);

    expect(ids(other)).toEqual(["e3"]);
  });

  /**
   * The infinite list is merged by id, never rewritten by position (CORR-281).
   *
   * Its pages 1..n are anchored on the cursor page 0 ended at, so the seam
   * between them is fixed. The handler used to replace page 0 with
   * `frame.slice(0, page0.length)`, which slid the window but not the seam:
   * rows pushed off the tail of page 0 landed in no page at all, and a frame
   * shorter than page 0 — the ordinary case, since the stream carries the
   * live in-memory list rather than the paged REST history — truncated it.
   * The rows did not merely leave the table: the KPI strip reduces over these
   * very pages flattened, so it reported a history with holes in it.
   */
  describe("the infinite list", () => {
    const KEY = ["executors-infinite", SERVER];
    const OLDEST = { id: "e4", controller_id: "grid_1", trading_pair: "SOL-USDC" };
    const ARCHIVED = { id: "e5", controller_id: "grid_1", trading_pair: "BTC-USDT" };

    type Row = { id: string };
    const cached = () =>
      queryClient.getQueryData<{ pages: { executors: Row[] }[] }>(KEY);
    const perPage = () => cached()?.pages.map((p) => p.executors.map((e) => e.id));
    const flat = () => cached()?.pages.flatMap((p) => p.executors.map((e) => e.id));

    /** Seed the cache the way the walk in `Bots.tsx` fills it: cursor-anchored pages. */
    const seed = (...pages: Row[][]) =>
      queryClient.setQueryData(KEY, {
        pages: pages.map((executors, i) => ({
          executors,
          next_cursor: i === pages.length - 1 ? null : `sds-offset:${i + 1}`,
        })),
        pageParams: pages.map((_, i) => (i === 0 ? "" : `sds-offset:${i}`)),
      });

    it("keeps every held row when the frame brings a new one at the head", () => {
      seed([SOL, OTHER], [OLDEST, ARCHIVED]);

      // A new executor was just created, so the frame leads with it.
      handleMessage(`executors:${SERVER}`, [BTC, SOL, OTHER]);

      expect(flat()).toEqual(["e1", "e2", "e3", "e4", "e5"]);
      expect(perPage()).toEqual([["e1", "e2", "e3"], ["e4", "e5"]]);
    });

    it("does not truncate page 0 to a shorter frame", () => {
      seed([BTC, SOL, OTHER]);

      // The live list is a different, smaller set than the paged history.
      handleMessage(`executors:${SERVER}`, [{ ...SOL, status: "COMPLETED" }]);

      expect(flat()).toEqual(["e1", "e2", "e3"]);
      expect(
        cached()?.pages[0].executors.map((e) => (e as { status?: string }).status),
      ).toEqual([undefined, "COMPLETED", undefined]);
    });

    it("refreshes a held row where it already sits, without re-adding it", () => {
      seed([BTC, SOL], [OLDEST]);

      handleMessage(`executors:${SERVER}`, [{ ...OLDEST, status: "COMPLETED" }]);

      expect(perPage()).toEqual([["e1", "e2"], ["e4"]]);
      expect(
        (cached()?.pages[1].executors[0] as { status?: string }).status,
      ).toBe("COMPLETED");
    });

    it("leaves no id twice across the pages", () => {
      // The walk can hand the same active executor out twice: the live list is
      // prepended in memory, the DB page repeats it lower down.
      seed([BTC, SOL], [SOL, OLDEST]);

      handleMessage(`executors:${SERVER}`, [BTC, SOL]);

      expect(flat()).toEqual(["e1", "e2", "e4"]);
    });

    it("leaves the pages alone when the frame carries nothing", () => {
      seed([BTC, SOL]);
      const before = cached();

      handleMessage(`executors:${SERVER}`, []);

      expect(cached()).toBe(before);
    });

    it("does not seed a list nobody has walked yet", () => {
      handleMessage(`executors:${SERVER}`, [BTC, SOL]);

      expect(cached()).toBeUndefined();
    });
  });
});

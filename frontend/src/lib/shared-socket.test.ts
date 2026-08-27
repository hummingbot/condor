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

  it("refreshes the first page of the infinite list", () => {
    queryClient.setQueryData(["executors-infinite", SERVER], {
      pages: [{ executors: [OTHER], next_cursor: null }],
      pageParams: [undefined],
    });

    handleMessage(`executors:${SERVER}`, [BTC, SOL]);

    expect(
      queryClient.getQueryData<{ pages: { executors: { id: string }[] }[] }>([
        "executors-infinite",
        SERVER,
      ])?.pages[0].executors.map((e) => e.id),
    ).toEqual(["e1"]);
  });
});

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
import { queryClient } from "./queryClient";
import { handleMessage } from "./shared-socket";

const SERVER = "prod";

function snapshot(
  controllerId: string,
  timestamp: string,
  pnl = 1,
): ControllerPerformanceSnapshot {
  return {
    timestamp,
    bot_name: "epsilon",
    controller_id: controllerId,
    controller_name: controllerId,
    connector: "binance",
    trading_pair: "BTC-USDT",
    realized_pnl_quote: pnl,
    unrealized_pnl_quote: 0,
    global_pnl_quote: pnl,
    global_pnl_pct: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    custom_info: {},
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
    // ControllerPnlChart.tsx: ["controller-perf-history", server, controllerId, deployedAt]
    const key = ["controller-perf-history", SERVER, "pmm_1", "2026-08-01T00:00:00.000Z"];
    queryClient.setQueryData(key, history([snapshot("pmm_1", "100")]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200", 5), snapshot("pmm_2", "200", 9)],
    });

    const merged = snapshotsAt(key);
    expect(merged?.map((s) => s.timestamp)).toEqual(["100", "200"]);
    // Only this controller's snapshot, not the sibling's
    expect(merged?.every((s) => s.controller_id === "pmm_1")).toBe(true);
  });

  it("deduplicates by controller_id + timestamp on a repeated frame", () => {
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
    const single = ["controller-perf-history", SERVER, "pmm_2", "d0"];
    queryClient.setQueryData(fleet, history([]));
    queryClient.setQueryData(single, history([]));

    handleMessage(`controller_perf:${SERVER}`, {
      snapshots: [snapshot("pmm_1", "200"), snapshot("pmm_2", "200")],
    });

    expect(snapshotsAt(fleet)).toHaveLength(2);
    expect(snapshotsAt(single)?.map((s) => s.controller_id)).toEqual(["pmm_2"]);
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

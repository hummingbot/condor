// ── Which source draws a scope's series, and what that source may not lose ──
//
// The three sources return the same `PnlChartPoint[]`, which is exactly why
// they need pinning: a fallback that looks identical to the real thing while
// meaning something weaker is the failure this module exists to prevent. What
// is pinned here is the ordering between them, the fact that the *reason* for a
// fallback survives to the caller, and the two mappings that must not be
// re-derived — the realized/unrealized split, and fees that were never measured.

import { describe, expect, it } from "vitest";

import type { PerformanceSnapshot } from "@/lib/api";
import type { ClosedOutcome, PnlChartPoint } from "@/lib/pnl-chart";
import {
  asControllerSnapshot,
  cumulativeFees,
  feesAreKnown,
  resolvePerfSeries,
  scopeInterval,
  scopeKey,
  snapshotSeries,
} from "@/lib/perf-history";

const T0 = Date.parse("2026-09-01T20:00:00.000Z");
const MIN = 60_000;

function row(overrides: Partial<PerformanceSnapshot> = {}): PerformanceSnapshot {
  return {
    timestamp: new Date(T0).toISOString(),
    subject: "executor",
    scope_id: "exec-1",
    status: "RUNNING",
    is_terminal: false,
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_quote: 0,
    cum_fees_quote: 0,
    bot_name: null,
    controller_id: "main",
    executor_id: "exec-1",
    executor_type: "position_executor",
    account_name: "master_account",
    connector_name: "lighter",
    trading_pair: "ETH-USDC",
    close_type: null,
    ...overrides,
  };
}

const point = (time: number): PnlChartPoint => ({
  time,
  realized: 1,
  unrealized: 0,
  total: 1,
  volume: 0,
  volumeDelta: 0,
  position: 0,
});

const closed = (endedAt: number, net: number): ClosedOutcome => ({
  endedAt,
  net,
  volume: 10,
  pair: "ETH-USDC",
});

describe("scopeKey", () => {
  it("keys an executor by its own id, never by a bot name it does not have", () => {
    // A bot name is deliberately not a join key between the two populations, so
    // an executor row carries none — and inventing one would group two
    // unrelated executors under a shared fabricated key.
    expect(scopeKey(row({ scope_id: "exec-1", bot_name: null }))).toBe(":exec-1");
  });

  it("keeps two executors under one controller apart", () => {
    const a = scopeKey(row({ scope_id: "exec-1" }));
    const b = scopeKey(row({ scope_id: "exec-2" }));
    expect(a).not.toBe(b);
  });

  it("reproduces the real composite for a controller row", () => {
    expect(
      scopeKey(row({ subject: "controller", bot_name: "bot-a", scope_id: "ctrl-1" })),
    ).toBe("bot-a:ctrl-1");
  });
});

describe("asControllerSnapshot", () => {
  it("passes the realized/unrealized split through untouched", () => {
    // Upstream makes this split from settlement, and POSITION_HOLD is the case
    // that makes re-deriving it wrong: the position was handed to
    // `position_holds`, so its PnL stays unrealized even though the executor
    // has closed. Reading `is_terminal` and calling it realized is the
    // double-count the upstream mapping exists to avoid.
    const held = row({
      is_terminal: true,
      status: "TERMINATED",
      close_type: "POSITION_HOLD",
      realized_pnl_quote: 0,
      unrealized_pnl_quote: 42,
      global_pnl_quote: 42,
    });
    const mapped = asControllerSnapshot(held);
    expect(mapped.realized_pnl_quote).toBe(0);
    expect(mapped.unrealized_pnl_quote).toBe(42);
  });

  it("carries volume_quote across as the one volume notion", () => {
    expect(asControllerSnapshot(row({ volume_quote: 1234 })).volume_traded).toBe(1234);
  });

  it("invents no position breakdown", () => {
    // These rows carry none, and synthesising one from the PnL would draw a
    // position nobody holds.
    expect(asControllerSnapshot(row({ global_pnl_quote: 99 })).positions_summary).toEqual([]);
  });
});

describe("snapshotSeries", () => {
  it("folds one executor's rows into an ascending series", () => {
    const series = snapshotSeries([
      row({ timestamp: new Date(T0 + 2 * MIN).toISOString(), unrealized_pnl_quote: 3 }),
      row({ timestamp: new Date(T0).toISOString(), unrealized_pnl_quote: 1 }),
      row({ timestamp: new Date(T0 + MIN).toISOString(), unrealized_pnl_quote: 2 }),
    ]);
    expect(series.map((p) => p.time)).toEqual([T0, T0 + MIN, T0 + 2 * MIN]);
    expect(series.map((p) => p.total)).toEqual([1, 2, 3]);
  });

  it("does not append a live 'now' point", () => {
    // The rows are the record. A synthetic point at Date.now() would draw a
    // flat line from the last dump to this instant, which for a closed
    // executor reads as a position still open.
    const series = snapshotSeries([row({ is_terminal: true, unrealized_pnl_quote: 5 })]);
    expect(series).toHaveLength(1);
    expect(series[0].time).toBe(T0);
  });

  it("sums two scopes at each instant rather than interleaving them", () => {
    const series = snapshotSeries([
      row({ scope_id: "exec-1", unrealized_pnl_quote: 1 }),
      row({ scope_id: "exec-2", unrealized_pnl_quote: 10 }),
    ]);
    expect(series).toHaveLength(1);
    expect(series[0].total).toBe(11);
  });

  it("is empty for no rows", () => {
    expect(snapshotSeries([])).toEqual([]);
  });
});

describe("scopeInterval", () => {
  const HOUR = 60 * MIN;

  it("asks for the minute rung the shared ladder does not have", () => {
    // `SAMPLING_INTERVALS` starts at 5m, which is the *controller* sampler's
    // grain. An executor is written every 60s, so a ten-minute one would be two
    // points at 5m and ten at 1m.
    expect(scopeInterval(T0, T0 + 10 * MIN)).toBe("1m");
  });

  it("hands over to the shared ladder once a minute would overrun the budget", () => {
    const coarse = scopeInterval(T0, T0 + 30 * 24 * HOUR);
    expect(coarse).not.toBe("1m");
    expect(["15m", "30m", "1h", "4h", "12h", "1d"]).toContain(coarse);
  });

  it("measures a running scope to now and a closed one to its close", () => {
    const now = T0 + 30 * 24 * HOUR;
    // Same start; the closed one stops at its close and stays fine-grained,
    // the running one is measured to now and coarsens.
    expect(scopeInterval(T0, T0 + 5 * MIN, now)).toBe("1m");
    expect(scopeInterval(T0, null, now)).not.toBe("1m");
  });

  it("does not change resolution as a closed executor ages", () => {
    // Its query key has to stay stable, or every re-render refetches.
    const ended = T0 + 5 * MIN;
    expect(scopeInterval(T0, ended, T0 + HOUR)).toBe(scopeInterval(T0, ended, T0 + 400 * HOUR));
  });

  it("falls to the finest rung for a scope that never said when it started", () => {
    expect(scopeInterval(null, null)).toBe("1m");
    expect(scopeInterval(undefined, undefined)).toBe("1m");
  });
});

describe("fees", () => {
  it("reports fees as unknown when every row says null", () => {
    // Controllers report null because `PerformanceReport` has no fees field.
    // Unknown is not zero: folding these with `?? 0` would draw a controller as
    // having traded for free.
    const controllerRows = [
      row({ subject: "controller", cum_fees_quote: null }),
      row({ subject: "controller", cum_fees_quote: null }),
    ];
    expect(feesAreKnown(controllerRows)).toBe(false);
    expect(cumulativeFees(controllerRows)).toBeNull();
  });

  it("distinguishes a measured zero from an unmeasured one", () => {
    expect(cumulativeFees([row({ cum_fees_quote: 0 })])).toBe(0);
  });

  it("takes each scope's newest running total, not the sum of every dump", () => {
    const rows = [
      row({ scope_id: "exec-1", timestamp: new Date(T0).toISOString(), cum_fees_quote: 1 }),
      row({ scope_id: "exec-1", timestamp: new Date(T0 + MIN).toISOString(), cum_fees_quote: 3 }),
      row({ scope_id: "exec-2", timestamp: new Date(T0).toISOString(), cum_fees_quote: 5 }),
    ];
    expect(cumulativeFees(rows)).toBe(8);
  });
});

describe("resolvePerfSeries", () => {
  it("prefers upstream snapshots over every fallback", () => {
    const result = resolvePerfSeries({
      snapshots: [row({ unrealized_pnl_quote: 7 })],
      controllerPoints: [point(T0)],
      outcomes: [closed(T0, 5)],
      supported: true,
    });
    expect(result.source).toBe("snapshots");
    expect(result.unsupported).toBe(false);
    expect(result.points[0].total).toBe(7);
  });

  it("falls back to the controller history when there are no snapshots", () => {
    const result = resolvePerfSeries({
      controllerPoints: [point(T0)],
      outcomes: [closed(T0, 5)],
      supported: true,
    });
    expect(result.source).toBe("controller-history");
  });

  it("falls back to closed outcomes only when nothing sampled answered", () => {
    const result = resolvePerfSeries({
      controllerPoints: [],
      outcomes: [closed(T0, 5), closed(T0 + 10 * MIN, 3)],
      supported: true,
    });
    expect(result.source).toBe("closed-outcomes");
    expect(result.points.at(-1)!.total).toBe(8);
  });

  it("says a fallback was taken because the server has no such route", () => {
    // The whole point of the probe reaching the notice: a reader looking at a
    // derived curve can tell whether the fix is upgrading their API.
    const result = resolvePerfSeries({
      outcomes: [closed(T0, 5)],
      supported: false,
    });
    expect(result.source).toBe("closed-outcomes");
    expect(result.unsupported).toBe(true);
  });

  it("does not call a server out of date while the probe is still in flight", () => {
    const result = resolvePerfSeries({ outcomes: [closed(T0, 5)], supported: undefined });
    expect(result.unsupported).toBe(false);
  });

  it("never marks a real snapshot series as unsupported", () => {
    // A stale "no" from the probe must not put a warning on a curve that was
    // just served by the route the probe claims is missing.
    const result = resolvePerfSeries({
      snapshots: [row({ unrealized_pnl_quote: 1 })],
      supported: false,
    });
    expect(result.source).toBe("snapshots");
    expect(result.unsupported).toBe(false);
  });

  it("reports 'none' rather than an empty curve that looks drawn", () => {
    const result = resolvePerfSeries({ supported: true });
    expect(result.source).toBe("none");
    expect(result.points).toEqual([]);
  });

  it("draws no borrowed curve for a running executor", () => {
    // The case FEAT-087 deletes. A live executor with no recorded series gets
    // nothing — not its parent controller's line under its own name.
    const result = resolvePerfSeries({
      snapshots: [],
      controllerPoints: undefined,
      outcomes: undefined,
      supported: true,
    });
    expect(result.points).toEqual([]);
    expect(result.source).toBe("none");
  });
});

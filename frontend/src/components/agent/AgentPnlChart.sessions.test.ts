import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionsToDataPoints } from "./AgentPnlChart";

// CORR-388: the equity curve used to space sessions one hour apart ending at
// Date.now(), and the chart printed those invented times on its axis.
describe("sessionsToDataPoints", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const rows = [
    { session_num: 2, total_pnl: -3, started_at: 1_752_000_000.9 },
    { session_num: 1, total_pnl: 10, started_at: 1_750_000_000.4 },
    { session_num: 3, total_pnl: 5.5, started_at: 1_754_000_000 },
  ];

  it("plots each session at its real start with the cumulative PnL", () => {
    expect(sessionsToDataPoints(rows)).toEqual([
      { time: 1_750_000_000, value: 10 },
      { time: 1_752_000_000, value: 7 },
      { time: 1_754_000_000, value: 12.5 },
    ]);
  });

  it("does not depend on the clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2020-01-01T00:00:00Z"));
    const first = sessionsToDataPoints(rows);
    vi.setSystemTime(new Date("2031-06-15T12:34:56Z"));
    const second = sessionsToDataPoints(rows);
    expect(second).toEqual(first);
  });

  it("leaves off a session with no known start", () => {
    const points = sessionsToDataPoints([
      { session_num: 1, total_pnl: 10, started_at: 1_750_000_000 },
      { session_num: 2, total_pnl: 4, started_at: 0 },
      { session_num: 3, total_pnl: 1 },
      { session_num: 4, total_pnl: 2, started_at: 1_751_000_000 },
    ]);
    expect(points.map((p) => p.time)).toEqual([1_750_000_000, 1_751_000_000]);
    expect(points.map((p) => p.value)).toEqual([10, 12]);
  });

  it("returns no points when no session carries a start (older backend)", () => {
    expect(
      sessionsToDataPoints([
        { session_num: 1, total_pnl: 10 },
        { session_num: 2, total_pnl: 4, started_at: 0 },
      ]),
    ).toEqual([]);
  });

  it("merges sessions that started in the same second into one ascending point", () => {
    expect(
      sessionsToDataPoints([
        { session_num: 2, total_pnl: 3, started_at: 1_750_000_000.7 },
        { session_num: 1, total_pnl: 1, started_at: 1_750_000_000.2 },
      ]),
    ).toEqual([{ time: 1_750_000_000, value: 4 }]);
  });
});

import { describe, expect, it } from "vitest";

import { formatDelegationTime } from "./delegationStatus";

// ARCH-404: the elapsed time goes through the shared `formatDuration`, so a
// task four hours in reads `4h12m` like the lab run rail, not a floored `4h`.
describe("formatDelegationTime", () => {
  const NOW_MS = 2_000_000_000_000;
  const started_at = NOW_MS / 1000 - 15_120;

  it("reads a running task's elapsed time in the shared units", () => {
    expect(formatDelegationTime({ started_at, status: "running" }, NOW_MS)).toBe("4h12m");
  });

  it("says how long ago a finished task started", () => {
    expect(formatDelegationTime({ started_at, status: "done" }, NOW_MS)).toBe("4h12m ago");
  });

  it("says nothing for a task with no start", () => {
    expect(formatDelegationTime({ started_at: 0, status: "running" }, NOW_MS)).toBe("");
  });
});

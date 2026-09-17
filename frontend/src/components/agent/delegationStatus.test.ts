import { describe, expect, it } from "vitest";

import {
  DELEGATION_STATUS,
  formatDelegationTime,
  isDelegationStatus,
} from "./delegationStatus";

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

// ARCH-398: the run rail narrows a raw status through the exhaustive map, not a
// hand-copied list, and a prototype key is not a status.
describe("isDelegationStatus", () => {
  it("accepts a mapped status and refuses anything else", () => {
    expect(isDelegationStatus("timeout")).toBe(true);
    expect(isDelegationStatus("bogus")).toBe(false);
    expect(isDelegationStatus("toString")).toBe(false);
  });

  it("accepts every status the map colours", () => {
    for (const s of Object.keys(DELEGATION_STATUS)) expect(isDelegationStatus(s)).toBe(true);
  });
});

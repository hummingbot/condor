import { describe, expect, it } from "vitest";

import { chartNotice, type ChartNoticeInput } from "@/lib/perf-notices";

/** A fleet scope with nothing to disclose; each case overrides what it is about. */
const base: ChartNoticeInput = {
  scopeKind: "fleet",
  population: "running",
  runHistory: null,
  seriesSource: "controller-history",
  capabilitySupported: true,
  execHistoryLoading: false,
  execHistoryError: false,
  truncated: false,
};

const at = (over: Partial<ChartNoticeInput>) => chartNotice({ ...base, ...over });

describe("chartNotice — a live fleet", () => {
  it("says nothing when the whole history is loaded", () => {
    expect(at({})).toBeUndefined();
  });

  it("discloses a history that stops short of the earliest deploy", () => {
    expect(at({ truncated: true })?.label).toBe("partial history");
  });

  it("stays silent for a controller scope with a complete history", () => {
    expect(at({ scopeKind: "controller" })).toBeUndefined();
  });
});

describe("chartNotice — a terminated run", () => {
  const terminated = { population: "terminated" } as const;

  it("says nothing when the run's own snapshots drew the curve", () => {
    expect(at({ ...terminated, runHistory: { source: "snapshots", points: 42 } })).toBeUndefined();
  });

  it("names the archived database when the curve was rebuilt from trades", () => {
    expect(at({ ...terminated, runHistory: { source: "archive", points: 42 } })?.label).toBe(
      "from the archived database",
    );
  });

  it("says there is no recorded history, and passes the server's reason through", () => {
    const notice = at({
      ...terminated,
      runHistory: { source: "none", points: 0, detail: "no archive db" },
    });
    expect(notice?.label).toBe("no recorded history");
    expect(notice?.detail).toContain("no archive db");
  });

  it("falls back to closed outcomes when no run history was fetched at all", () => {
    expect(at({ ...terminated, runHistory: null })?.label).toBe("closed outcomes");
  });

  it("falls back to closed outcomes when the history came back empty", () => {
    expect(at({ ...terminated, runHistory: { source: "snapshots", points: 0 } })?.label).toBe(
      "closed outcomes",
    );
  });

  // The terminated selection is only reached by a non-executor scope: an
  // executor on a finished run answers for its own series instead.
  it("yields to the executor selection when the scope is an executor", () => {
    expect(
      at({
        ...terminated,
        scopeKind: "executor",
        seriesSource: "snapshots",
        runHistory: { source: "archive", points: 42 },
      }),
    ).toBeUndefined();
  });
});

describe("chartNotice — an executor scope", () => {
  const executor = { scopeKind: "executor" } as const;

  it("says nothing when the executor's own sampled series was drawn", () => {
    expect(at({ ...executor, seriesSource: "snapshots" })).toBeUndefined();
  });

  it("blames the API build when it records no executor history at all", () => {
    const notice = at({ ...executor, seriesSource: "none", capabilitySupported: false });
    expect(notice?.label).toBe("no recorded series");
    expect(notice?.detail).toContain("This API does not record executor performance");
  });

  it("says nothing while the executor's history is still in flight", () => {
    expect(
      at({ ...executor, seriesSource: "none", execHistoryLoading: true }),
    ).toBeUndefined();
  });

  it("reports an empty result as the executor never having been recorded", () => {
    const notice = at({ ...executor, seriesSource: "none" });
    expect(notice?.label).toBe("no recorded series");
    expect(notice?.detail).toContain("has none for this one");
  });

  // The correction CORR-299 asked for: a request that failed establishes
  // nothing about the executor, so it must not be reported as absence.
  it("names a rejected fetch as a failure, not as an absence", () => {
    const errored = at({ ...executor, seriesSource: "none", execHistoryError: true });
    expect(errored?.label).toBe("history unavailable");
    expect(errored?.detail).not.toContain("has none for this one");
  });

  it("names an unreachable upstream, which arrives in band as a 200", () => {
    const notice = at({
      ...executor,
      seriesSource: "none",
      execHistoryServerOnline: false,
      execHistoryErrorHint: "Connection error: timed out",
    });
    expect(notice?.label).toBe("history unavailable");
    expect(notice?.detail).toContain("Connection error: timed out");
  });

  it("says nothing while the capability probe is still unanswered", () => {
    expect(
      at({ ...executor, seriesSource: "none", capabilitySupported: undefined }),
    ).toBeUndefined();
  });

  it("prefers the capability answer over an error", () => {
    expect(
      at({
        ...executor,
        seriesSource: "none",
        capabilitySupported: false,
        execHistoryError: true,
      })?.detail,
    ).toContain("This API does not record executor performance");
  });
});

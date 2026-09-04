/**
 * Deleting a run must take its controllers off the Terminated tree (CORR-298).
 *
 * The tree's spine is `["terminated-controllers", server]`, not the runs query
 * the delete used to invalidate, and `leafFromTerminatedController` builds a
 * leaf for a bot with no run record at all — so the deleted run's bot,
 * controllers and KPIs stayed on screen while its header and Delete button
 * vanished. These pin the cache eviction against a real `QueryClient`.
 */

import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { dropDeletedRunQueries } from "./run-deletion";

const SERVER = "srv";
const BOT = "gan-2026.08.21_18.05";

function seeded() {
  const qc = new QueryClient();
  qc.setQueryData(["bot-runs", SERVER], { runs: [] });
  qc.setQueryData(["terminated-controllers", SERVER], { controllers: [] });
  qc.setQueryData(["terminated-controllers", "other"], { controllers: [] });
  qc.setQueryData(["run-history", SERVER, BOT, "2026-08-21T18:05:02+00:00"], { rows: 1 });
  qc.setQueryData(["run-history", SERVER, "kept", "2026-08-21T18:05:02+00:00"], { rows: 1 });
  return qc;
}

const invalidated = (qc: QueryClient, key: unknown[]) =>
  qc.getQueryState(key)?.isInvalidated === true;

describe("dropDeletedRunQueries", () => {
  it("invalidates the terminated controllers listing, not only the runs", () => {
    const qc = seeded();
    dropDeletedRunQueries(qc, SERVER, BOT);

    expect(invalidated(qc, ["bot-runs", SERVER])).toBe(true);
    expect(invalidated(qc, ["terminated-controllers", SERVER])).toBe(true);
  });

  it("removes the deleted run's history rather than refetching it", () => {
    const qc = seeded();
    dropDeletedRunQueries(qc, SERVER, BOT);

    expect(qc.getQueryData(["run-history", SERVER, BOT, "2026-08-21T18:05:02+00:00"])).toBe(
      undefined,
    );
    expect(
      qc.getQueryData(["run-history", SERVER, "kept", "2026-08-21T18:05:02+00:00"]),
    ).toEqual({ rows: 1 });
  });

  it("leaves another server's listing alone", () => {
    const qc = seeded();
    dropDeletedRunQueries(qc, SERVER, BOT);

    expect(invalidated(qc, ["terminated-controllers", "other"])).toBe(false);
  });

  it("still refreshes the tree when the deleted run's bot name is unknown", () => {
    const qc = seeded();
    dropDeletedRunQueries(qc, SERVER, undefined);

    expect(invalidated(qc, ["terminated-controllers", SERVER])).toBe(true);
    expect(qc.getQueryData(["run-history", SERVER, BOT, "2026-08-21T18:05:02+00:00"])).toEqual({
      rows: 1,
    });
  });
});

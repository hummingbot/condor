// ── What a deleted run must take with it out of the cache ──

import type { QueryClient } from "@tanstack/react-query";

/**
 * Drop every cached answer that still believes a just-deleted run exists.
 *
 * Deleting a run used to invalidate `["bot-runs", server]` alone, and that is
 * only the run *record*. The Terminated tree's spine is a different query —
 * `["terminated-controllers", server]`, polled every 60s behind a 30s
 * `staleTime` — and `leafFromTerminatedController` happily builds a leaf for a
 * bot with no run record, so the deleted run's controllers, KPIs and chart kept
 * standing in the sidebar while its header and actions vanished with the runs
 * refetch. A delete that leaves its subject on screen for up to a minute reads
 * as a delete that did nothing.
 *
 * The run's own history is *removed* rather than invalidated: it is keyed
 * `["run-history", server, bot_name, created_at]` with `staleTime: Infinity`
 * because a finished run's curve is immutable, and refetching a run that no
 * longer exists could only ever return an empty answer. The key is passed as a
 * prefix so every `created_at` under that bot goes with it.
 *
 * Kept out of `PerfBrowser` so it can be tested at all: that module exports a
 * component, and `react-refresh/only-export-components` forbids it a second
 * export.
 */
export function dropDeletedRunQueries(
  queryClient: QueryClient,
  server: string,
  botName?: string,
): void {
  queryClient.invalidateQueries({ queryKey: ["bot-runs", server] });
  queryClient.invalidateQueries({ queryKey: ["terminated-controllers", server] });
  if (botName) queryClient.removeQueries({ queryKey: ["run-history", server, botName] });
}

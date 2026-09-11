// ── What you can do to an executor, wherever it is listed ──
//
// Stopping and exporting used to live inside `pages/Executors.tsx`, which is
// also where the only executor table lived. The browser lists executors under
// every scope that has them (FEAT-086), so both moved out here rather than
// being reimplemented beside the new table.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { api, type ExecutorInfo } from "@/lib/api";

/** The columns the export writes, in order. */
const CSV_HEADERS = [
  "ID", "Type", "Controller", "Connector", "Pair", "Side", "Status", "Close Type",
  "PnL", "PnL%", "Volume", "Fees", "Entry Price", "Current Price", "Timestamp",
];

/** Download a set of executors as CSV, one row each. */
export function exportExecutorsCsv(executors: ExecutorInfo[], filename = "executors.csv") {
  const rows = executors.map((ex) => [
    ex.id,
    ex.type,
    ex.controller_id,
    ex.connector,
    ex.trading_pair,
    ex.side,
    ex.status,
    ex.close_type,
    ex.pnl.toFixed(4),
    ex.net_pnl_pct ? (ex.net_pnl_pct * 100).toFixed(2) + "%" : "",
    ex.volume.toFixed(2),
    ex.cum_fees_quote.toFixed(4),
    ex.entry_price || "",
    ex.current_price || "",
    ex.timestamp ? new Date(ex.timestamp * 1000).toISOString() : "",
  ]);
  const csv = [CSV_HEADERS, ...rows].map((r) => r.map((c) => `"${c}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export interface ExecutorStop {
  /** Ids with a stop in flight, so their rows can say so. */
  stoppingIds: Set<string>;
  /** The ids a confirmation is currently armed for, or null. */
  pendingIds: string[] | null;
  /** Whatever went wrong with the last stop, already phrased for a reader. */
  error: string | null;
  pending: boolean;
  /** Arm the confirmation for these ids. A stop is never issued without it. */
  request: (ids: string[]) => void;
  confirm: (ids: string[], keepPosition: boolean) => void;
  cancel: () => void;
}

/**
 * Stopping executors, with the keep-position choice the dialog asks for.
 *
 * Every stop goes through `request` → the dialog → `confirm`, because the two
 * outcomes are not the same thing at all: one closes the position on the
 * exchange and one leaves it open, and the difference is invisible afterwards.
 *
 * Failures are reported per batch rather than per id — `Promise.allSettled`, so
 * one rejection does not abandon the rest — and the ids are cleared from
 * `stoppingIds` in `onSettled` whichever way it went, so a failed stop does not
 * leave a row spinning forever.
 */
export function useExecutorStop(server: string): ExecutorStop {
  const queryClient = useQueryClient();
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [pendingIds, setPendingIds] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async ({ ids, keepPosition }: { ids: string[]; keepPosition: boolean }) => {
      setError(null);
      setStoppingIds((prev) => new Set([...prev, ...ids]));
      return Promise.allSettled(ids.map((id) => api.stopExecutor(server, id, keepPosition)));
    },
    onSuccess: (results, vars) => {
      const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
      if (failed.length > 0) {
        const reason = failed[0].reason;
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(
          `Failed to stop ${failed.length} of ${vars.ids.length} executor${vars.ids.length === 1 ? "" : "s"}: ${message}`,
        );
      }
    },
    onSettled: (_data, _error, vars) => {
      setStoppingIds((prev) => {
        const next = new Set(prev);
        vars?.ids.forEach((id) => next.delete(id));
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ["executors-infinite", server] });
    },
  });

  const request = useCallback((ids: string[]) => {
    if (ids.length > 0) setPendingIds(ids);
  }, []);

  const confirm = useCallback(
    (ids: string[], keepPosition: boolean) => {
      setPendingIds(null);
      mutation.mutate({ ids, keepPosition });
    },
    [mutation],
  );

  const cancel = useCallback(() => setPendingIds(null), []);

  return {
    stoppingIds,
    pendingIds,
    error,
    pending: mutation.isPending,
    request,
    confirm,
    cancel,
  };
}

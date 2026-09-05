import { useCallback, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { RoutineInstance } from "@/lib/api";

/** The one thing that charts an archived run in Condor (FEAT-079). */
export const ARCHIVED_ROUTINE = "archived_analyzer";

/** Statuses a routine instance can still come back from. */
const IN_FLIGHT = new Set(["running", "pending"]);

function inFlight(instance: RoutineInstance | null | undefined): boolean {
  return !!instance && IN_FLIGHT.has(instance.status);
}

/**
 * The report for one archived run, or one controller inside it.
 *
 * An archived run is immutable, so charting one is "generate once, look it up
 * forever": the lookup asks whether a report already exists for this (server,
 * db, controller), and only a miss runs the routine. That is the same move the
 * Backtesting tab made — the dashboard is a *caller* of the one thing that
 * charts an archive, not a second implementation of it — and it is why the
 * 695-line React chart component could go.
 *
 * A miss is the ordinary case, not an error: nobody has charted this subject
 * yet, or the report that did has been pruned past by retention. Either way the
 * caller gets `reportId: null` and offers a Chart button.
 *
 * The run is followed through its routine instance, exactly as `useBacktest`
 * follows a backtest — but the handover needs no effect. A settled run's id
 * joins the lookup's query key, so finishing *is* what re-asks the store
 * whether a report now exists; that the subject was stamped is proven by the
 * store rather than assumed from the instance.
 */
export function useArchivedReport(
  server: string | null | undefined,
  dbPath: string,
  controllerId = "",
) {
  const [instanceId, setInstanceId] = useState<string | null>(null);

  // The run this browser launched, while it runs. Polls at the routine's own
  // cadence and stops the moment the instance settles.
  const { data: instance } = useQuery({
    queryKey: ["routine-instance", instanceId],
    queryFn: () => api.getRoutineInstance(instanceId!),
    enabled: !!instanceId,
    refetchInterval: (query) =>
      IN_FLIGHT.has(query.state.data?.status ?? "running") ? 2000 : false,
  });

  const settled = instance && !inFlight(instance) ? instance.instance_id : null;

  const { data: stored, isLoading } = useQuery({
    queryKey: ["archived-report", server, dbPath, controllerId, settled],
    queryFn: () => api.getArchivedReport(server!, dbPath, controllerId),
    enabled: !!server && !!dbPath,
    staleTime: Infinity,
  });

  const chart = useMutation({
    mutationFn: () =>
      // `chart: false` because the dashboard embeds the report itself — no
      // Telegram photo, no kaleido render nobody will look at.
      api.runRoutine(server!, ARCHIVED_ROUTINE, {
        mode: "detail",
        db_path: dbPath,
        controller_id: controllerId,
        chart: false,
      }),
    onSuccess: (data) => {
      if (data.instance_id) setInstanceId(data.instance_id);
    },
  });

  // Depend on `mutate`, not on the object `useMutation` returns: that object is
  // spread fresh on every render, while `mutate` is memoized on the observer.
  // Depending on the wrapper would rebuild this callback — and so the
  // `chart`/`regenerate` handed to callers — on every single render. The
  // arg-less wrapper stays, so a button's click event is never forwarded as the
  // mutation's variables.
  const { mutate } = chart;
  const run = useCallback(() => mutate(), [mutate]);

  // A failed run keeps the story, and its own words: "no such database" is
  // advice, where a blank panel would just invite the same failure again.
  const failure =
    instance?.status === "failed" ? instance.error ?? "Run failed" : null;

  return {
    reportId: stored?.report_id ?? null,
    createdAt: stored?.created_at ?? null,
    title: stored?.title ?? "",
    /** Still asking whether a report exists. Not the same as "none exists". */
    isLoading,
    /** A run of the routine is in flight for this subject. */
    isRunning: chart.isPending || inFlight(instance),
    error: chart.error?.message ?? failure,
    chart: run,
    regenerate: run,
  };
}

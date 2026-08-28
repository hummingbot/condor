import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { BacktestSummary, BacktestTask, RoutineInstance } from "@/lib/api";

/** The one thing that runs a backtest in Condor (FEAT-076). */
export const BACKTEST_ROUTINE = "backtest_chart";

export interface SubmitBacktestPayload {
  config_id: string;
  /** `YYYY-MM-DD`, exactly as the form holds them — the routine speaks dates. */
  start_date: string;
  end_date: string;
  backtesting_resolution?: string;
  trade_cost?: number;
}

function dateToTs(dateStr: string): number {
  return Math.floor(new Date(dateStr).getTime() / 1000);
}

/**
 * The server-side `task_id` a finished run produced.
 *
 * `backtest_chart` returns its metrics as a one-row table whose first column is
 * `task_id` (FEAT-039's agent-facing contract). Read by key rather than by
 * position: `METRIC_COLUMNS` is free to grow or reorder, and the dashboard is
 * now one of its readers.
 */
export function taskIdFromInstance(
  instance: RoutineInstance | null | undefined,
): string | null {
  const row = instance?.table_data?.[0];
  const taskId = row?.task_id;
  return typeof taskId === "string" && taskId ? taskId : null;
}

/** Statuses a routine instance can still come back from. */
const IN_FLIGHT = new Set(["running", "pending"]);

/**
 * An in-flight (or failed) run, in the shape the task list already renders.
 *
 * Its id is the *instance* id: a run has no `task_id` until the engine has
 * accepted it, and the row has to exist from the moment it is launched. When
 * it completes, the archive entry under the real task id takes over.
 */
function instanceRow(instance: RoutineInstance): BacktestSummary {
  const config = (instance.config ?? {}) as Record<string, unknown>;
  const date = (key: string) => {
    const value = config[key];
    return typeof value === "string" ? dateToTs(value) : null;
  };
  return {
    task_id: instance.instance_id,
    instance_id: instance.instance_id,
    status: instance.status === "failed" ? "failed" : "running",
    server: instance.server_name ?? "",
    config: {
      config: { id: String(config.config_name ?? "") },
      start_time: date("start_date"),
      end_time: date("end_date"),
      backtesting_resolution: String(config.resolution ?? ""),
      trade_cost: config.trade_cost,
    },
    created_at: instance.created_at,
    error: instance.error ?? null,
  };
}

/**
 * The detail panel's view of a run that has not produced a payload yet.
 *
 * The routine's own error text is surfaced verbatim: a run that outlived the
 * poll timeout names its `task_id` and says to render it later, which is advice
 * a generic "backtest failed" would throw away.
 */
function instanceAsTask(
  instance: RoutineInstance | null | undefined,
  row: BacktestSummary | undefined,
): BacktestTask | undefined {
  const status = instance?.status ?? row?.status;
  if (!status) return undefined;
  return {
    task_id: instance?.instance_id ?? row!.task_id,
    status: status === "failed" ? "failed" : "running",
    config: (instance ? instanceRow(instance).config : row?.config) ?? {},
    error: instance?.error ?? row?.error ?? undefined,
  };
}

/**
 * Data layer for the Backtesting tab.
 *
 * A backtest is launched by running the `backtest_chart` routine (FEAT-076),
 * not by submitting a task to a server's API: one driver means a dashboard run
 * is saved the moment it completes, inherits the routine's retry-on-stall
 * polling and its timed-out-result recovery, and leaves a handle behind that
 * outlives the browser tab.
 *
 * That leaves three queries. `backtest-instances` is every in-flight run,
 * whoever launched it — Telegram and agent runs included, which the tab was
 * blind to while it only knew its own server's tasks — and it polls every 5 s.
 * `backtest-archive` is every saved run the user can reach on *any* server; it
 * carries no server in its key because a saved backtest is not server-scoped,
 * and it settles rather than polls. Neither response carries a payload: opening
 * a run is what fetches one, from the archive.
 *
 * The component keeps its own form/UI state and hands the form payload to
 * `submit`, keeping the hook free of presentation concerns.
 */
export function useBacktest(server: string | null | undefined) {
  const queryClient = useQueryClient();

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pinnedTaskId, setPinnedTaskId] = useState<string | null>(null);
  // The run this browser just launched, until the instances query catches up.
  // `submit` resolves with an instance id that no list has seen yet, and
  // without this the tab would spend a round trip asking the archive for an id
  // that is not a task id at all.
  const [launchedInstanceId, setLaunchedInstanceId] = useState<string | null>(null);

  // Available configs
  const { data: configsData } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
  });

  // Every backtest run currently in flight, from any seat.
  const { data: instances, isLoading: instancesLoading } = useQuery({
    queryKey: ["backtest-instances"],
    queryFn: () => api.getRoutineInstances(),
    refetchInterval: 5000,
  });

  // The archive: every server, no payloads. Reading a run never needs the sidebar.
  const { data: archive, isLoading: archiveLoading } = useQuery({
    queryKey: ["backtest-archive"],
    queryFn: () => api.listBacktestArchive(),
    staleTime: 30000,
    refetchInterval: 30000,
  });

  const instanceRows = useMemo<BacktestSummary[]>(
    () =>
      (instances ?? [])
        .filter(
          (inst) =>
            inst.routine_name === BACKTEST_ROUTINE &&
            (IN_FLIGHT.has(inst.status) || inst.status === "failed"),
        )
        .map(instanceRow),
    [instances],
  );

  // One list. A run in flight and a run in the archive are disjoint by
  // construction — the archive entry only exists once the run has completed,
  // and it is filed under a different id.
  const tasks = useMemo<BacktestSummary[]>(
    () => [...instanceRows, ...(archive?.summaries ?? [])],
    [instanceRows, archive],
  );

  const selectedEntry = tasks.find((t) => t.task_id === selectedTaskId);
  const pinnedEntry = tasks.find((t) => t.task_id === pinnedTaskId);

  // Whether the selection names a routine instance rather than a stored run.
  const selectedInstanceId =
    selectedTaskId &&
    (selectedTaskId === launchedInstanceId || !!selectedEntry?.instance_id)
      ? selectedTaskId
      : null;

  // A pruned payload is not worth a request: the list already knows there is
  // no chart to draw, and the metrics it does have are in the summary.
  const selectedHasPayload = selectedEntry?.has_payload !== false;
  const pinnedHasPayload = pinnedEntry?.has_payload !== false;

  // The selected instance, while it runs. Polls at the routine's own cadence;
  // stops the moment it is no longer running.
  const { data: selectedInstance } = useQuery({
    queryKey: ["backtest-instance", selectedInstanceId],
    queryFn: () => api.getRoutineInstance(selectedInstanceId!),
    enabled: !!selectedInstanceId,
    refetchInterval: (query) =>
      IN_FLIGHT.has(query.state.data?.status ?? "running") ? 2000 : false,
  });

  // Selected run's payload, from the archive — whichever server ran it.
  const { data: fetchedTask, isLoading: selectedTaskLoading } = useQuery({
    queryKey: ["backtest-task", selectedTaskId],
    queryFn: () => api.getArchivedBacktest(selectedTaskId!),
    enabled: !!selectedTaskId && !selectedInstanceId && selectedHasPayload,
  });

  const selectedTask = selectedInstanceId
    ? instanceAsTask(selectedInstance, selectedEntry)
    : fetchedTask;

  // Pinned task detail (for comparison). Only a stored run can be pinned.
  const { data: pinnedTask } = useQuery({
    queryKey: ["backtest-task", pinnedTaskId],
    queryFn: () => api.getArchivedBacktest(pinnedTaskId!),
    enabled:
      !!pinnedTaskId &&
      pinnedTaskId !== selectedTaskId &&
      pinnedHasPayload &&
      !pinnedEntry?.instance_id,
  });

  const invalidateLists = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["backtest-instances"] });
    queryClient.invalidateQueries({ queryKey: ["backtest-archive"] });
  }, [queryClient]);

  // A finished run hands over to its archive entry: the instance is the handle
  // while it runs, the task id is the handle forever after.
  useEffect(() => {
    if (!selectedInstance || IN_FLIGHT.has(selectedInstance.status)) return;
    const taskId = taskIdFromInstance(selectedInstance);
    if (!taskId) return; // failed: the instance keeps the story, and the error
    setSelectedTaskId(taskId);
    setLaunchedInstanceId((id) =>
      id === selectedInstance.instance_id ? null : id,
    );
    invalidateLists();
  }, [selectedInstance, invalidateLists]);

  // Auto-select first completed task
  useEffect(() => {
    if (!selectedTaskId && tasks.length > 0) {
      const completed = tasks.find((t) => t.status === "completed");
      setSelectedTaskId(completed?.task_id ?? tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  // Submit: run the routine, which is what every other seat does too. `chart:
  // false` because the tab draws its own — no Telegram photo, no PNG cost.
  const submitMutation = useMutation({
    mutationFn: (payload: SubmitBacktestPayload) =>
      api.runRoutine(server!, BACKTEST_ROUTINE, {
        config_name: payload.config_id,
        start_date: payload.start_date,
        end_date: payload.end_date,
        resolution: payload.backtesting_resolution,
        trade_cost: payload.trade_cost,
        chart: false,
      }),
    onSuccess: (data) => {
      invalidateLists();
      if (data.instance_id) {
        setLaunchedInstanceId(data.instance_id);
        setSelectedTaskId(data.instance_id);
      }
    },
  });

  // Delete. A stored run is deleted from the archive; an in-flight one has
  // nothing stored yet, so the same gesture stops the run instead — which also
  // drops its instance, and with it the row.
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => {
      const entry = tasks.find((t) => t.task_id === taskId);
      return entry?.instance_id
        ? api.stopRoutineInstance(entry.instance_id)
        : api.deleteArchivedBacktest(taskId);
    },
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      if (pinnedTaskId === taskId) setPinnedTaskId(null);
      if (launchedInstanceId === taskId) setLaunchedInstanceId(null);
      invalidateLists();
    },
  });

  return {
    configsData,
    tasks,
    tasksLoading: instancesLoading || archiveLoading,
    /** False while the v2 archive index is still being built in the background. */
    indexing: archive ? !archive.migrated : false,
    selectedTask,
    selectedTaskLoading,
    selectedTaskId,
    setSelectedTaskId,
    selectedEntry,
    /** The selected run kept its metrics but lost its chart data to retention. */
    selectedChartExpired: !!selectedEntry && !selectedHasPayload,
    setPinnedTaskId,
    pinnedTask,
    pinnedTaskId,
    submit: submitMutation,
    remove: deleteMutation,
  };
}

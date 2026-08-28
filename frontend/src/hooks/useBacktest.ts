import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { BacktestSummary } from "@/lib/api";

export interface SubmitBacktestPayload {
  config_id: string;
  start_time: number;
  end_time: number;
  backtesting_resolution?: string;
  trade_cost?: number;
}

/**
 * Data layer for the Backtesting tab.
 *
 * The list and the payload are two different queries because they are two
 * different objects (FEAT-075). `backtest-live` is the selected server's
 * running tasks — it polls every 5 s because a running backtest changes that
 * often. `backtest-archive` is every saved run the user can reach on *any*
 * server; it carries no server in its key because a saved backtest is not
 * server-scoped, and it settles rather than polls. Neither response carries a
 * payload: opening a run is what fetches one.
 *
 * The component keeps its own form/UI state and hands the form payload to
 * `submit`, keeping the hook free of presentation concerns.
 */
export function useBacktest(server: string | null | undefined) {
  const queryClient = useQueryClient();

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pinnedTaskId, setPinnedTaskId] = useState<string | null>(null);

  // Available configs
  const { data: configsData } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
  });

  // Live tasks on the selected server (already merged with that server's saved runs)
  const { data: liveTasks, isLoading: liveLoading } = useQuery({
    queryKey: ["backtest-live", server],
    queryFn: () => api.listBacktestTasks(server!),
    enabled: !!server,
    refetchInterval: 5000,
  });

  // The archive: every server, no payloads. Reading a run never needs the sidebar.
  const { data: archive, isLoading: archiveLoading } = useQuery({
    queryKey: ["backtest-archive"],
    queryFn: () => api.listBacktestArchive(),
    staleTime: 30000,
    refetchInterval: 30000,
  });

  // One list. A live entry wins over its archived twin: it is the one that can
  // still be pending, and it is the fresher of the two.
  const tasks = useMemo<BacktestSummary[]>(() => {
    const byId = new Map<string, BacktestSummary>();
    for (const summary of archive?.summaries ?? []) byId.set(summary.task_id, summary);
    for (const task of liveTasks ?? []) byId.set(task.task_id, task);
    return [...byId.values()];
  }, [liveTasks, archive]);

  const selectedEntry = tasks.find((t) => t.task_id === selectedTaskId);
  const pinnedEntry = tasks.find((t) => t.task_id === pinnedTaskId);

  /**
   * Where a run's payload comes from. A saved run on another server has to go
   * through the archive (the per-server route 404s it on purpose, SEC-197);
   * anything on the current server goes through the server route, which is
   * also what auto-saves a run that has only just finished.
   */
  const fetchTask = (entry: BacktestSummary | undefined, taskId: string) =>
    entry?.saved && entry.server && entry.server !== server
      ? api.getArchivedBacktest(taskId)
      : server
        ? api.getBacktestTask(server, taskId)
        : api.getArchivedBacktest(taskId);

  // A pruned payload is not worth a request: the list already knows there is
  // no chart to draw, and the metrics it does have are in the summary.
  const selectedHasPayload = selectedEntry?.has_payload !== false;
  const pinnedHasPayload = pinnedEntry?.has_payload !== false;

  // Selected task detail (polls every 2s while pending/running)
  const { data: selectedTask, isLoading: selectedTaskLoading } = useQuery({
    queryKey: ["backtest-task", selectedTaskId],
    queryFn: () => fetchTask(selectedEntry, selectedTaskId!),
    enabled: !!selectedTaskId && selectedHasPayload,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "pending" || status === "running") return 2000;
      return false;
    },
  });

  // Pinned task detail (for comparison)
  const { data: pinnedTask } = useQuery({
    queryKey: ["backtest-task", pinnedTaskId],
    queryFn: () => fetchTask(pinnedEntry, pinnedTaskId!),
    enabled: !!pinnedTaskId && pinnedTaskId !== selectedTaskId && pinnedHasPayload,
  });

  // Auto-select first completed task
  useEffect(() => {
    if (!selectedTaskId && tasks.length > 0) {
      const completed = tasks.find((t) => t.status === "completed");
      setSelectedTaskId(completed?.task_id ?? tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  const invalidateLists = () => {
    queryClient.invalidateQueries({ queryKey: ["backtest-live", server] });
    queryClient.invalidateQueries({ queryKey: ["backtest-archive"] });
  };

  // Submit mutation
  const submitMutation = useMutation({
    mutationFn: (payload: SubmitBacktestPayload) =>
      api.submitBacktest(server!, payload),
    onSuccess: (data) => {
      invalidateLists();
      if (data.task_id) setSelectedTaskId(data.task_id);
    },
  });

  // Delete mutation. Same routing rule as the read: a run from another server
  // is deleted through the archive, not through the sidebar's server.
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => {
      const entry = tasks.find((t) => t.task_id === taskId);
      if (server && (!entry?.server || entry.server === server)) {
        return api.deleteBacktestTask(server, taskId);
      }
      return api.deleteArchivedBacktest(taskId);
    },
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      if (pinnedTaskId === taskId) setPinnedTaskId(null);
      invalidateLists();
    },
  });

  return {
    configsData,
    tasks,
    tasksLoading: liveLoading || archiveLoading,
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

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { BacktestTask } from "@/lib/api";

export interface SubmitBacktestPayload {
  config_id: string;
  start_time: number;
  end_time: number;
  backtesting_resolution?: string;
  trade_cost?: number;
}

/**
 * Data layer for the Backtesting tab. Encapsulates the four backtest queries
 * (available configs, task list, selected task, pinned task), the submit/delete
 * mutations, their query keys and per-server invalidation, plus the
 * selection/pinning state those queries and mutations depend on.
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

  // Task list
  const { data: tasks, isLoading: tasksLoading } = useQuery({
    queryKey: ["backtest-tasks", server],
    queryFn: () => api.listBacktestTasks(server!),
    enabled: !!server,
    refetchInterval: 5000,
  });

  // Selected task detail (polls every 2s while pending/running)
  const { data: selectedTask, isLoading: selectedTaskLoading } = useQuery({
    queryKey: ["backtest-task", server, selectedTaskId],
    queryFn: () => api.getBacktestTask(server!, selectedTaskId!),
    enabled: !!server && !!selectedTaskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "pending" || status === "running") return 2000;
      return false;
    },
  });

  // Pinned task detail (for comparison)
  const { data: pinnedTask } = useQuery({
    queryKey: ["backtest-task", server, pinnedTaskId],
    queryFn: () => api.getBacktestTask(server!, pinnedTaskId!),
    enabled: !!server && !!pinnedTaskId && pinnedTaskId !== selectedTaskId,
  });

  // Auto-select first completed task
  useEffect(() => {
    if (!selectedTaskId && tasks && tasks.length > 0) {
      const completed = tasks.find((t) => t.status === "completed");
      setSelectedTaskId(completed?.task_id ?? tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  // Submit mutation
  const submitMutation = useMutation({
    mutationFn: (payload: SubmitBacktestPayload) =>
      api.submitBacktest(server!, payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["backtest-tasks", server] });
      if (data.task_id) setSelectedTaskId(data.task_id);
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.deleteBacktestTask(server!, taskId),
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      if (pinnedTaskId === taskId) setPinnedTaskId(null);
      queryClient.invalidateQueries({ queryKey: ["backtest-tasks", server] });
    },
  });

  return {
    configsData,
    tasks: tasks as BacktestTask[] | undefined,
    tasksLoading,
    selectedTask,
    selectedTaskLoading,
    selectedTaskId,
    setSelectedTaskId,
    pinnedTask,
    pinnedTaskId,
    setPinnedTaskId,
    submit: submitMutation,
    remove: deleteMutation,
  };
}

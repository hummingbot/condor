import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { type ExecutorInfo, api } from "@/lib/api";
import { executorsQuery } from "@/lib/queryClient";

/**
 * Hook to get real-time executor data for an agent by subscribing to the
 * existing executors:{server} WS channel and filtering by controller IDs.
 *
 * Falls back to REST polling if WS is not connected.
 */
export function useAgentExecutors(
  serverName: string | null | undefined,
  controllerIds: string[],
): { executors: ExecutorInfo[]; isLoading: boolean } {
  // Subscribe to the executors WS channel for this server
  const channel = serverName ? `executors:${serverName}` : "";
  const channels = useMemo(() => (channel ? [channel] : []), [channel]);
  useCondorWebSocket(channels, serverName ?? null);

  // Read from React Query cache — the shared socket already pushes every
  // `executors:<server>` frame into this exact (unfiltered) entry.
  const { data: allExecutors, isLoading } = useQuery({
    queryKey: executorsQuery(serverName).queryKey,
    queryFn: () => api.getExecutors(serverName!),
    enabled: !!serverName,
    refetchInterval: 10000, // Fallback polling
  });

  // Filter executors to those matching the agent's controller IDs
  const filtered = useMemo(() => {
    if (!allExecutors || controllerIds.length === 0) return [];
    const idSet = new Set(controllerIds);
    return (allExecutors as ExecutorInfo[]).filter(
      (ex) => ex.controller_id && idSet.has(ex.controller_id),
    );
  }, [allExecutors, controllerIds]);

  return { executors: filtered, isLoading };
}

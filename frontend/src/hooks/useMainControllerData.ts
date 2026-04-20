import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ExecutorInfo } from "@/lib/api";
import { computeMultiOverlays } from "@/lib/executor-overlays";

export function useMainControllerData(
  server: string | null,
  connector: string,
  pair: string,
) {
  const queryClient = useQueryClient();

  // Read executors from React Query cache (populated by WS subscription)
  const cachedExecutors = queryClient.getQueryData<ExecutorInfo[]>(["executors", server, ""]);

  const executors = useMemo(() => {
    if (!cachedExecutors) return [];
    return cachedExecutors.filter(
      (ex) =>
        ex.controller_id === "main" &&
        ex.connector === connector &&
        ex.trading_pair === pair,
    );
  }, [cachedExecutors, connector, pair]);

  const overlays = useMemo(() => computeMultiOverlays(executors), [executors]);

  // Fetch consolidated positions
  const { data: positionsData, isLoading: isLoadingPositions } = useQuery({
    queryKey: ["consolidated-positions", server],
    queryFn: () => api.getConsolidatedPositions(server!),
    enabled: !!server,
    refetchInterval: 10_000,
  });

  const positions = useMemo(() => {
    if (!positionsData) return [];
    const all = [
      ...(positionsData.executor_positions ?? []),
      ...(positionsData.bot_positions ?? []),
    ];
    return all.filter(
      (p) =>
        p.controller_id === "main" &&
        p.connector_name === connector &&
        p.trading_pair === pair,
    );
  }, [positionsData, connector, pair]);

  return { executors, overlays, positions, isLoadingPositions };
}

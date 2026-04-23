import { useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type ExecutorInfo } from "@/lib/api";
import { computeMultiOverlays } from "@/lib/executor-overlays";

/** Build a fingerprint string for an executor array to detect real changes */
function executorsFingerprint(exs: ExecutorInfo[]): string {
  return exs
    .map((e) => `${e.id}:${e.status}:${e.pnl}:${e.entry_price}:${e.current_price}:${e.close_timestamp}`)
    .join("|");
}

export function useMainControllerData(
  server: string | null,
  connector: string,
  pair: string,
) {
  // Subscribe to executors cache (populated by WS subscription)
  // enabled:false means no fetch — data comes from WS setQueryData
  const { data: cachedExecutors } = useQuery<ExecutorInfo[]>({
    queryKey: ["executors", server, ""],
    enabled: false,
  });

  const filteredExecutors = useMemo(() => {
    if (!cachedExecutors) return [];
    return cachedExecutors.filter(
      (ex) =>
        ex.controller_id === "main" &&
        ex.connector === connector &&
        ex.trading_pair === pair,
    );
  }, [cachedExecutors, connector, pair]);

  // Stable reference: only update when executor data actually changes
  const prevFingerprintRef = useRef("");
  const stableExecutorsRef = useRef<ExecutorInfo[]>([]);

  const executors = useMemo(() => {
    const fp = executorsFingerprint(filteredExecutors);
    if (fp !== prevFingerprintRef.current) {
      prevFingerprintRef.current = fp;
      stableExecutorsRef.current = filteredExecutors;
    }
    return stableExecutorsRef.current;
  }, [filteredExecutors]);

  const overlays = useMemo(() => computeMultiOverlays(executors), [executors]);

  // Fetch consolidated positions
  const { data: positionsData, isLoading: isLoadingPositions } = useQuery({
    queryKey: ["consolidated-positions", server],
    queryFn: () => api.getConsolidatedPositions(server!),
    enabled: !!server,
    refetchInterval: 5_000,
    staleTime: 0,
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

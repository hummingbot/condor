import { useMemo, useRef } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

import { api, type ExecutorInfo } from "@/lib/api";
import { computeMultiOverlays } from "@/lib/executor-overlays";

/** Build a fingerprint string for an executor array to detect real changes */
function executorsFingerprint(exs: ExecutorInfo[]): string {
  return exs
    .map((e) => `${e.id}:${e.status}:${e.pnl}:${e.entry_price}:${e.current_price}:${e.close_timestamp}`)
    .join("|");
}

/** The pool an executor traded in, from wherever it records one. */
function executorPool(ex: ExecutorInfo): string {
  return (
    (ex.config?.pool_address as string | undefined) ||
    (ex.custom_info?.pool_address as string | undefined) ||
    ""
  );
}

interface Options {
  /**
   * A second spelling of the same market, queried alongside `pair`.
   *
   * One DEX market has two names: the `<base_mint>-<quote>` form a pool-first UI
   * builds, and the `<base_symbol>-<quote>` form an executor created from
   * Telegram or MCP carries. Neither is wrong and neither can be derived from the
   * other by string work, so both are asked for and the results merged --
   * otherwise a live LP position is simply invisible on its own pool's page.
   */
  altPair?: string;
  /**
   * Keep only the liquidity positions held in this exact pool.
   *
   * On a pool-first page the pair is not the identity -- `SOL-USDC` names dozens
   * of pools -- and a position lives in exactly one of them, so an LP executor
   * that records a different pool is dropped. Swaps are not filtered: an order is
   * a fact about the pair, and the router picks its own pool for it.
   */
  poolAddress?: string;
}

export function useMainControllerData(
  server: string | null,
  connector: string,
  pair: string,
  { altPair = "", poolAddress = "" }: Options = {},
) {
  // Fetch executors filtered server-side by controller_id + trading_pair
  const { data: cachedExecutors } = useQuery<ExecutorInfo[]>({
    queryKey: ["executors", server, "main", pair],
    queryFn: () => api.getExecutors(server!, { controller_id: "main", trading_pair: pair }),
    enabled: !!server && !!pair,
    staleTime: 30_000, // REST fetch valid for 30s, WS pushes override instantly
    refetchOnWindowFocus: false,
  });

  // Same query under the market's other name. The WS bridge refreshes every
  // 4-element ["executors", server, controller, pair] key it finds, so this one
  // stays as live as the primary without any wiring of its own.
  const wantsAlt = !!altPair && altPair !== pair;
  const { data: altExecutors } = useQuery<ExecutorInfo[]>({
    queryKey: ["executors", server, "main", altPair],
    queryFn: () => api.getExecutors(server!, { controller_id: "main", trading_pair: altPair }),
    enabled: !!server && wantsAlt,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  // connector and pool are not server-side filters — apply them client-side
  const filteredExecutors = useMemo(() => {
    const seen = new Set<string>();
    const rows: ExecutorInfo[] = [];
    for (const ex of [...(cachedExecutors ?? []), ...(wantsAlt ? altExecutors ?? [] : [])]) {
      if (seen.has(ex.id)) continue;
      seen.add(ex.id);
      if (ex.connector !== connector) continue;
      if (poolAddress && ex.type?.toLowerCase() === "lp") {
        const pool = executorPool(ex);
        if (pool && pool !== poolAddress) continue;
      }
      rows.push(ex);
    }
    return rows;
  }, [cachedExecutors, altExecutors, wantsAlt, connector, poolAddress]);

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
    placeholderData: keepPreviousData, // keep showing old data during refetch/refresh
  });

  const positions = useMemo(() => {
    if (!positionsData) return [];
    const all = [
      ...(positionsData.executor_positions ?? []),
      ...(positionsData.bot_positions ?? []),
    ];
    // A position is named the same two ways its executor is, so both spellings
    // of the market count as this market.
    const pairs = new Set([pair, ...(wantsAlt ? [altPair] : [])]);
    return all.filter(
      (p) =>
        // Show positions from main controller or untagged (executor-level positions)
        (!p.controller_id || p.controller_id === "main") &&
        p.connector_name === connector &&
        pairs.has(p.trading_pair),
    );
  }, [positionsData, connector, pair, altPair, wantsAlt]);

  return { executors, overlays, positions, isLoadingPositions };
}

import { useMemo } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

import {
  LP_EXECUTOR_TYPE,
  LP_REFRESH_MS,
  RECENT_LP_EXECUTORS,
} from "@/components/dex/lp-position";
import { api, type ExecutorInfo } from "@/lib/api";
import { computeMultiOverlays } from "@/lib/executor-overlays";

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
   * The pool the page is about, which is also how its LP positions are found.
   *
   * Two jobs. It keeps only the liquidity held in *this* pool: on a pool-first
   * page the pair is not the identity -- `SOL-USDC` names dozens of pools -- and
   * a position lives in exactly one of them, so an LP executor recording a
   * different pool is dropped. Swaps are not filtered: an order is a fact about
   * the pair, and the router picks its own pool for it.
   *
   * It is also queried on directly, because no spelling of the pair reliably
   * finds an LP position -- see the by-pool query below.
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

  /**
   * Every recent LP executor on the server, narrowed to the ones in this pool.
   *
   * Neither pair query above is guaranteed to find a position. The API's pair
   * filter is case-sensitive and matches whatever spelling the *creator* used,
   * while `altPair` is built from the pool row, whose symbols are upper-cased --
   * so a real position in `GMEx-USDC` is asked for as `GMEX-USDC` and comes back
   * empty, and the pool page renders no box on the chart and no row in the table.
   * The pool address is the one identifier both sides spell identically, so it is
   * matched on directly here.
   *
   * Same query as the `/dex` strip, key included, so arriving from there -- the
   * usual way into a pool you hold -- costs no extra fetch.
   */
  const { data: poolLpExecutors } = useQuery<ExecutorInfo[]>({
    queryKey: ["dex-lp-executors", server],
    queryFn: () =>
      api.getExecutors(server!, {
        executor_type: LP_EXECUTOR_TYPE,
        limit: RECENT_LP_EXECUTORS,
      }),
    enabled: !!server && !!poolAddress,
    refetchInterval: LP_REFRESH_MS,
    staleTime: LP_REFRESH_MS,
  });

  // connector and pool are not server-side filters — apply them client-side.
  //
  // No identity stabiliser sits on top of this: react-query's structural
  // sharing (on by default) hands back the *same* array reference when a
  // refetch returns deep-equal JSON, so an idle market already re-runs this
  // memo zero times. The hand-rolled fingerprint+ref that used to guard it
  // compared six fields, which made it both redundant here and a staleness
  // trap -- a change to any seventh field kept serving the old rows.
  const executors = useMemo(() => {
    // The by-pool rows are the whole server's LP history, so they are admitted
    // only on an exact pool match -- unlike the pair rows, which were already
    // scoped by the query that fetched them.
    const inThisPool = poolAddress
      ? (poolLpExecutors ?? []).filter((ex) => executorPool(ex) === poolAddress)
      : [];
    // Upsert by id, by-pool rows last: they refresh on their own 20s poll,
    // while the pair queries sit still between WS pushes -- so when both hold
    // the same executor, the by-pool copy replaces the pair copy instead of
    // being shadowed by it. Map keeps first-insertion order, so row order is
    // unchanged from the dedup this replaces.
    const byId = new Map<string, ExecutorInfo>();
    for (const ex of [
      ...(cachedExecutors ?? []),
      ...(wantsAlt ? (altExecutors ?? []) : []),
      ...inThisPool,
    ]) {
      byId.set(ex.id, ex);
    }
    const rows: ExecutorInfo[] = [];
    for (const ex of byId.values()) {
      if (ex.connector !== connector) continue;
      if (poolAddress && ex.type?.toLowerCase() === "lp") {
        const pool = executorPool(ex);
        if (pool && pool !== poolAddress) continue;
      }
      rows.push(ex);
    }
    return rows;
  }, [cachedExecutors, altExecutors, poolLpExecutors, wantsAlt, connector, poolAddress]);

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
    // A position is named however its executor is, and an executor found by pool
    // may spell the market a third way neither `pair` nor `altPair` predicted --
    // so the spellings that actually turned up count as this market too.
    const pairs = new Set([
      pair,
      ...(wantsAlt ? [altPair] : []),
      ...executors.map((ex) => ex.trading_pair).filter(Boolean),
    ]);
    return all.filter(
      (p) =>
        // Show positions from main controller or untagged (executor-level positions)
        (!p.controller_id || p.controller_id === "main") &&
        p.connector_name === connector &&
        pairs.has(p.trading_pair),
    );
  }, [positionsData, connector, pair, altPair, wantsAlt, executors]);

  return { executors, overlays, positions, isLoadingPositions };
}

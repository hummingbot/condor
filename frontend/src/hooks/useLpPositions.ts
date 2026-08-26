import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { poolLabel } from "@/components/dex/format";
import {
  LP_EXECUTOR_TYPE,
  LP_REFRESH_MS,
  RECENT_LP_EXECUTORS,
  readLpPosition,
  type LpPosition,
} from "@/components/dex/lp-position";
import { api, type PoolSummary } from "@/lib/api";
import { isExecutorActive } from "@/lib/formatters";

/** GeckoTerminal's multi-pool endpoint, which the labels come from, caps here. */
const MAX_LABELLED_POOLS = 30;

/**
 * A pair label for a position, preferring the pool's own.
 *
 * An LP executor's `trading_pair` is `<base_mint>-<quote_symbol>` — the form the
 * executor needs, and unreadable as a label: `So1111…112-USDC` rather than
 * `SOL-USDC`. The pool row has the symbols, so it wins when it resolved.
 */
function labelFor(pos: LpPosition, pool: PoolSummary | undefined): string {
  if (pool) return poolLabel(pool);
  const [poolBase, poolQuote] = pos.pair.split("-");
  if (poolBase && poolBase.length > 12) {
    return `${poolBase.slice(0, 4)}…-${poolQuote ?? ""}`;
  }
  return pos.pair || pos.poolAddress.slice(0, 8);
}

/**
 * Every LP range Condor currently holds on a server, biggest first.
 *
 * The reading is here rather than in a renderer because two surfaces show the
 * same list in two shapes — the strip above `/dex`'s pool browser and the
 * portfolio's liquidity table — and two copies of "which LP executor counts as
 * open" is how they would come to disagree about whether a range is live.
 *
 * Deliberately *not* derived from the page-wide `["executors", server, ""]`
 * cache that Portfolio already holds: unfiltered, that route reads the newest
 * 500 executors of every kind, so a range opened last week on a busy server
 * falls off the end of the page and silently vanishes. The typed query below
 * bypasses that cache and 200 *LP* executors reaches far deeper in time.
 */
export function useLpPositions(server: string | null): {
  positions: LpPosition[];
  label: (pos: LpPosition) => string;
  /** The venue as the pool row names it — a fallback for a missing `provider`. */
  dexId: (pos: LpPosition) => string;
  isLoading: boolean;
} {
  const { data: executors = [], isLoading } = useQuery({
    queryKey: ["dex-lp-executors", server],
    queryFn: () =>
      api.getExecutors(server!, {
        executor_type: LP_EXECUTOR_TYPE,
        limit: RECENT_LP_EXECUTORS,
      }),
    enabled: !!server,
    refetchInterval: LP_REFRESH_MS,
    staleTime: LP_REFRESH_MS,
  });

  const positions = useMemo(() => {
    const open: LpPosition[] = [];
    for (const ex of executors) {
      if (!isExecutorActive(ex.status)) continue;
      const pos = readLpPosition(ex);
      if (pos) open.push(pos);
    }
    // Biggest first: the position most worth checking is the one with the most
    // in it, and an unvalued position sorts last rather than to the top.
    open.sort((a, b) => (b.valueQuote ?? -1) - (a.valueQuote ?? -1));
    return open;
  }, [executors]);

  // Every open position is on one chain in practice (Gateway's CLMM connectors
  // are Solana-only), but the labels are fetched per chain so a second one does
  // not silently fall back to mint addresses.
  const byNetwork = useMemo(() => {
    const groups: Record<string, string[]> = {};
    for (const pos of positions.slice(0, MAX_LABELLED_POOLS)) {
      const list = (groups[pos.network] ??= []);
      if (!list.includes(pos.poolAddress)) list.push(pos.poolAddress);
    }
    return groups;
  }, [positions]);

  const { data: pools = {} } = useQuery({
    queryKey: ["dex-lp-pools", server, JSON.stringify(byNetwork)],
    queryFn: async () => {
      const entries = await Promise.all(
        Object.entries(byNetwork).map(([network, addresses]) =>
          api
            .getDexPoolsByAddress(server!, network, addresses)
            .then((r) => r.pools)
            .catch(() => [] as PoolSummary[]),
        ),
      );
      const map: Record<string, PoolSummary> = {};
      for (const pool of entries.flat()) map[pool.address] = pool;
      return map;
    },
    enabled: !!server && Object.keys(byNetwork).length > 0,
    // A pair label does not change; only the TVL beside it does.
    staleTime: 5 * 60_000,
  });

  const label = useMemo(
    () => (pos: LpPosition) => labelFor(pos, pools[pos.poolAddress]),
    [pools],
  );

  const dexId = useMemo(
    () => (pos: LpPosition) => pools[pos.poolAddress]?.dex_id ?? "",
    [pools],
  );

  return { positions, label, dexId, isLoading };
}

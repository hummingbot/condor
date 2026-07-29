import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type Ticker } from "@/lib/api";

/**
 * 24h tickers for a connector, already sorted by USD volume (highest first) by the
 * backend. Shared by PairSelector and MarketsPanel so both hit one cached query.
 */
export function useTickers(server: string, connector: string) {
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["tickers", server, connector],
    queryFn: () => api.getTickers(server, connector),
    enabled: !!server && !!connector,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  });

  const tickers = data?.tickers ?? [];

  const byPair = useMemo(() => {
    const map = new Map<string, Ticker>();
    for (const t of tickers) map.set(t.trading_pair, t);
    return map;
  }, [tickers]);

  /** Rank by volume: 0 = highest. Used to order pair lists. */
  const rankByPair = useMemo(() => {
    const map = new Map<string, number>();
    tickers.forEach((t, i) => map.set(t.trading_pair, i));
    return map;
  }, [tickers]);

  return {
    tickers,
    byPair,
    rankByPair,
    updatedAt: data?.updated_at ?? null,
    isLoading,
    isFetching,
    hasTickers: tickers.length > 0,
  };
}

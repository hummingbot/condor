import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

const GRID_STORAGE_KEY = "condor_grid_defaults";
const DEFAULT_CONNECTOR = "binance_perpetual";
const DEFAULT_PAIR = "BTC-USDT";
const DEFAULT_INTERVAL = "5m";
const DEFAULT_LOOKBACK = 3 * 86400; // 3 days

function getTradeDefaults() {
  try {
    const raw = localStorage.getItem(GRID_STORAGE_KEY);
    if (!raw) return { connector: DEFAULT_CONNECTOR, pair: DEFAULT_PAIR, interval: DEFAULT_INTERVAL, lookback: DEFAULT_LOOKBACK };
    const saved = JSON.parse(raw);
    return {
      connector: saved.connector || DEFAULT_CONNECTOR,
      pair: saved.pair || DEFAULT_PAIR,
      interval: saved.interval || DEFAULT_INTERVAL,
      lookback: saved.lookbackSeconds || DEFAULT_LOOKBACK,
    };
  } catch {
    return { connector: DEFAULT_CONNECTOR, pair: DEFAULT_PAIR, interval: DEFAULT_INTERVAL, lookback: DEFAULT_LOOKBACK };
  }
}

/**
 * Prefetches core data when the app loads so pages render instantly
 * instead of showing a loading state on first visit.
 *
 * Executors, bots, connectors, trading rules, and default candles
 * are all fetched eagerly as soon as a server is selected.
 */
export function usePrefetchData() {
  const { server } = useServer();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!server) return;

    const defaults = getTradeDefaults();

    // Core data
    queryClient.prefetchQuery({
      queryKey: ["executors", server, ""],
      queryFn: () => api.getExecutors(server),
    });

    queryClient.prefetchQuery({
      queryKey: ["bots", server],
      queryFn: () => api.getBots(server),
    });

    // Market data: connectors + trading rules
    queryClient.prefetchQuery({
      queryKey: ["connected-exchanges", server],
      queryFn: () => api.getConnectedExchanges(server),
    });

    queryClient
      .fetchQuery({
        queryKey: ["connectors", server],
        queryFn: () => api.getConnectors(server),
        staleTime: 5 * 60 * 1000,
      })
      .then((connectors) => {
        if (!connectors?.length) return;
        // Prefetch trading rules for each connector
        for (const connector of connectors) {
          queryClient.prefetchQuery({
            queryKey: ["trading-rules", server, connector],
            queryFn: () => api.getTradingRules(server, connector),
            staleTime: 5 * 60 * 1000,
          });
        }
      })
      .catch(() => {});

    // Prefetch candles for the default trade pair
    const startTime = Math.floor(Date.now() / 1000) - defaults.lookback;
    queryClient.prefetchQuery({
      queryKey: ["candles", server, defaults.connector, defaults.pair, defaults.interval],
      queryFn: () =>
        api.getCandles(server, defaults.connector, defaults.pair, defaults.interval, 5000, startTime),
    });
  }, [server, queryClient]);
}

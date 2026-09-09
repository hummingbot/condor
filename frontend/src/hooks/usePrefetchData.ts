import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { executorsQuery } from "@/lib/queryClient";

/**
 * Prefetches core data when the app loads so pages render instantly
 * instead of showing a loading state on first visit.
 *
 * Every prefetch here must warm a key some component actually observes,
 * otherwise it is a request whose only destination is the garbage collector.
 * Two used not to (PERF-335): a 5,000-row candles fetch keyed with a null
 * `endTime`, which no `candlesQuery` caller ever asks for (ExecutorChart always
 * passes both bounds, and TradeChart bypasses react-query entirely, fetching
 * straight into `candleStore`), and `["connectors", server]`, which no
 * component declares. Warming the /trade chart, if ever wanted again, has to go
 * through `candleStore.mergeCandles` under `candleChannelKey(...)` — the path
 * that chart actually reads.
 */
export function usePrefetchData() {
  const { server } = useServer();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!server) return;

    // Core data
    queryClient.prefetchQuery({
      queryKey: executorsQuery(server).queryKey,
      queryFn: () => api.getExecutors(server),
    });

    queryClient.prefetchQuery({
      queryKey: ["bots", server],
      queryFn: () => api.getBots(server),
    });

    // Prefetch trading rules only for connected exchanges (with credentials),
    // not all candle connectors — avoids 404s for unconfigured connectors
    queryClient
      .fetchQuery({
        queryKey: ["connected-exchanges", server],
        queryFn: () => api.getConnectedExchanges(server),
        staleTime: 5 * 60 * 1000,
      })
      .then((connectors) => {
        if (!connectors?.length) return;
        for (const connector of connectors) {
          queryClient.prefetchQuery({
            queryKey: ["trading-rules", server, connector],
            queryFn: () => api.getTradingRules(server, connector),
            staleTime: 5 * 60 * 1000,
          });
        }
      })
      .catch(() => {});

    // Prefetch settings data so Settings page loads instantly
    queryClient.prefetchQuery({
      queryKey: ["settings-servers"],
      queryFn: () => api.getSettingsServers(),
      staleTime: 60 * 1000,
    });
    queryClient.prefetchQuery({
      queryKey: ["settings-credentials", server],
      queryFn: () => api.getCredentials(server),
      staleTime: 5 * 60 * 1000,
    });
    queryClient.prefetchQuery({
      queryKey: ["settings-connectors", server, "spot"],
      queryFn: () => api.getAvailableConnectors(server, "spot"),
      staleTime: 5 * 60 * 1000,
    });
    queryClient.prefetchQuery({
      queryKey: ["settings-connectors", server, "perpetual"],
      queryFn: () => api.getAvailableConnectors(server, "perpetual"),
      staleTime: 5 * 60 * 1000,
    });
  }, [server, queryClient]);
}

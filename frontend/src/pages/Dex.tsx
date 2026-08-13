import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { NoServerCard } from "@/components/NoServerCard";
import { PoolBrowser } from "@/components/dex/PoolBrowser";
import {
  type PoolSource,
  PoolSourceTabs,
} from "@/components/dex/PoolSourceTabs";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

/** GeckoTerminal rate-limits per IP across every viewer and every polling chart. */
const POOL_STALE_MS = 30_000;
const SEARCH_DEBOUNCE_MS = 400;

/**
 * Browse pools, because on Gateway the pool *is* the decision.
 *
 * A venue with an order book has one canonical SOL-USDC; Gateway has dozens
 * across Meteora, Orca, Raydium and Uniswap with different fee tiers, bin steps,
 * TVL and APR. So this page is pool-first: pick one, and the workspace behind it
 * pins the chart, the stats and both executors to that exact pool.
 */
export function Dex() {
  const { server } = useServer();
  const [source, setSource] = useState<PoolSource>({
    kind: "gecko",
    view: "trending",
  });
  const [network, setNetwork] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  // A browser that refetches per keystroke would exhaust the same GeckoTerminal
  // budget the candle poll loop depends on.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  const { data: venues = [] } = useQuery({
    queryKey: ["venues", server],
    queryFn: () => api.getVenues(server!),
    enabled: !!server,
    staleTime: 5 * 60 * 1000,
  });

  // The chains to offer are the venues Trade does *not* take: the complement of
  // `hummingbotMarketData`, exactly as CreateExecutor computes it. Never a
  // hardcoded list.
  const networks = useMemo(
    () => venues.filter((v) => !v.hummingbotMarketData).map((v) => v.name),
    [venues],
  );

  // Derived, not synced: venues arrive after the first render, so the selection
  // falls back to the first chain until the user picks one that exists.
  const activeNetwork =
    network && networks.includes(network) ? network : (networks[0] ?? "");

  const isTokenSearch = source.kind === "gecko" && source.view === "token";
  const enabled =
    !!server &&
    (source.kind === "gateway" ||
      (!!activeNetwork && (!isTokenSearch || !!debouncedQuery)));

  const { data: pools = [], isFetching } = useQuery({
    queryKey: [
      "dex-pools",
      server,
      source.kind,
      source.kind === "gecko" ? source.view : source.connector,
      activeNetwork,
      debouncedQuery,
    ],
    queryFn: () =>
      api.getDexPools(
        server!,
        source.kind === "gecko"
          ? {
              source: "gecko",
              network: activeNetwork,
              view: source.view,
              query: isTokenSearch ? debouncedQuery : undefined,
            }
          : {
              source: "gateway",
              connector: source.connector,
              query: debouncedQuery || undefined,
            },
      ),
    enabled,
    staleTime: POOL_STALE_MS,
  });

  if (!server) {
    return <NoServerCard message="Select a server from the sidebar to browse DEX pools." />;
  }

  const emptyMessage = isTokenSearch && !debouncedQuery
    ? "Paste a token address to find its pools."
    : "No pools found.";

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">DEX</h1>
        <p className="text-sm text-[var(--color-text-muted)]">
          Pools on the networks Condor trades through Gateway. Open one to chart
          it, swap in it and provide liquidity to it.
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <PoolSourceTabs
          source={source}
          onSourceChange={setSource}
          networks={networks}
          network={activeNetwork}
          onNetworkChange={setNetwork}
          query={query}
          onQueryChange={setQuery}
        />
        <PoolBrowser
          pools={pools}
          isLoading={isFetching && !pools.length}
          emptyMessage={emptyMessage}
          showGatewayColumns={source.kind === "gateway"}
        />
      </div>
    </div>
  );
}

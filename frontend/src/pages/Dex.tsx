import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { PoolBrowser } from "@/components/dex/PoolBrowser";
import {
  type PoolSource,
  PoolSourceTabs,
} from "@/components/dex/PoolSourceTabs";
import { useServer } from "@/hooks/useServer";
import { api, type PoolSummary } from "@/lib/api";
import { useDexFavorites } from "@/lib/dexFavorites";

/** GeckoTerminal rate-limits per IP across every viewer and every polling chart. */
const POOL_STALE_MS = 30_000;
const SEARCH_DEBOUNCE_MS = 400;
const PAGE_SIZE = 20;

/**
 * The one chain the browser offers, deliberately.
 *
 * Every other Gateway network is reachable by URL (`/dex/{network}/{address}`
 * still renders), but the venues Condor can actually LP in — Meteora, Orca —
 * are Solana's, so a chain picker offering chains with no CLMM connector behind
 * them sells a workspace that cannot open a position. One constant, so widening
 * this later is a list, not a rewrite.
 */
const NETWORK = "solana-mainnet-beta";

/** An EVM address or a Solana pubkey — the same guard the backend applies. */
const ADDRESS_RE = /^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/;

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
  const navigate = useNavigate();
  const { favorites } = useDexFavorites();
  const [source, setSource] = useState<PoolSource>({
    kind: "gecko",
    view: "trending",
  });
  const [dexes, setDexes] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);

  // A browser that refetches per keystroke would exhaust the same GeckoTerminal
  // budget the candle poll loop depends on.
  useEffect(() => {
    const t = setTimeout(
      () => setDebouncedQuery(query.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(t);
  }, [query]);

  // Page 4 of Trending is not page 4 of anything else, and a stale page number
  // would land the user on an empty table.
  useEffect(() => {
    setPage(1);
  }, [source, debouncedQuery, dexes]);

  const { data: venues = [] } = useQuery({
    queryKey: ["dex-venues", server, NETWORK],
    queryFn: () => api.getDexVenues(server!, NETWORK),
    enabled: !!server,
    staleTime: 6 * 60 * 60 * 1000,
  });

  const isSearch = source.kind === "gecko" && source.view === "token";
  const isAddress = ADDRESS_RE.test(debouncedQuery);
  const enabled =
    !!server &&
    source.kind !== "favorites" &&
    (source.kind === "gateway" || !isSearch || isAddress);

  const { data: pagedPools, isFetching } = useQuery({
    queryKey: [
      "dex-pools",
      server,
      source.kind,
      source.kind === "gecko"
        ? source.view
        : source.kind === "gateway"
          ? source.connector
          : "favorites",
      debouncedQuery,
      dexes.join(","),
      page,
    ],
    queryFn: () =>
      api.getDexPools(
        server!,
        source.kind === "gateway"
          ? {
              source: "gateway",
              connector: source.connector,
              query: debouncedQuery || undefined,
              limit: PAGE_SIZE,
              page,
            }
          : {
              source: "gecko",
              network: NETWORK,
              view: source.kind === "gecko" ? source.view : "trending",
              query: isSearch ? debouncedQuery : undefined,
              dexes,
              limit: PAGE_SIZE,
              page,
            },
      ),
    enabled,
    staleTime: POOL_STALE_MS,
    placeholderData: (prev) => prev,
  });

  // The pasted address may be a *pool*, not a token. Both are 44 base58
  // characters and nothing distinguishes them by shape, so the pool lookup runs
  // alongside the token search and whichever resolves is what the user meant.
  const { data: pastedPool } = useQuery({
    queryKey: ["dex-pool-by-address", server, NETWORK, debouncedQuery],
    queryFn: () =>
      api
        .getDexPoolByAddress(server!, debouncedQuery, NETWORK)
        .catch(() => null as PoolSummary | null),
    enabled: !!server && isSearch && isAddress,
    staleTime: POOL_STALE_MS,
  });

  const favoriteAddresses = useMemo(
    () => favorites.filter((f) => f.network === NETWORK).map((f) => f.address),
    [favorites],
  );

  const { data: favoritePools = [], isFetching: favoritesFetching } = useQuery({
    queryKey: ["dex-favorites", server, NETWORK, favoriteAddresses.join(",")],
    queryFn: () =>
      api.getDexPoolsByAddress(server!, NETWORK, favoriteAddresses),
    enabled:
      !!server && source.kind === "favorites" && !!favoriteAddresses.length,
    staleTime: POOL_STALE_MS,
  });

  if (!server) {
    return (
      <NoServerCard message="Select a server from the sidebar to browse DEX pools." />
    );
  }

  const isFavorites = source.kind === "favorites";
  const pools = isFavorites ? favoritePools : (pagedPools?.pools ?? []);
  const loading = isFavorites
    ? favoritesFetching && !favoritePools.length
    : isFetching && !pagedPools;

  const emptyMessage = isFavorites
    ? "No favorites yet — star a pool to keep it here."
    : isSearch && !isAddress
      ? debouncedQuery
        ? "That is not a pool or token address."
        : "Paste a pool or token address to find it."
      : "No pools found.";

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">DEX</h1>
        <p className="text-sm text-[var(--color-text-muted)]">
          Solana pools Condor trades through Gateway. Open one to chart it, swap
          in it and provide liquidity to it.
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <PoolSourceTabs
          source={source}
          onSourceChange={setSource}
          venues={venues}
          selectedDexes={dexes}
          onDexesChange={setDexes}
          query={query}
          onQueryChange={setQuery}
          favoriteCount={favoriteAddresses.length}
        />

        {/* A pasted pool address is an answer, not a search result: it goes above
            the table with one obvious thing to do to it. */}
        {pastedPool && (
          <button
            onClick={() =>
              navigate(
                `/dex/${pastedPool.gateway_network || NETWORK}/${pastedPool.address}`,
              )
            }
            className="flex w-full items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <span className="flex items-center gap-2 text-sm">
              <span className="font-medium">
                {pastedPool.base_symbol && pastedPool.base_symbol !== "???"
                  ? `${pastedPool.base_symbol}-${pastedPool.quote_symbol}`
                  : pastedPool.name}
              </span>
              <span className="text-xs text-[var(--color-text-muted)]">
                {pastedPool.dex_id}
              </span>
            </span>
            <span className="flex items-center gap-1 text-xs text-[var(--color-primary)]">
              Open pool
              <ArrowRight className="h-3 w-3" />
            </span>
          </button>
        )}

        <PoolBrowser
          pools={pools}
          isLoading={loading}
          emptyMessage={emptyMessage}
          showGatewayColumns={source.kind === "gateway"}
          network={NETWORK}
          page={isFavorites ? 1 : page}
          hasMore={isFavorites ? false : !!pagedPools?.has_more}
          onPageChange={setPage}
        />
      </div>
    </div>
  );
}

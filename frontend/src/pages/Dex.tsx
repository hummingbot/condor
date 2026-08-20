import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { LpPositions } from "@/components/dex/LpPositions";
import { PoolBrowser } from "@/components/dex/PoolBrowser";
import { PoolSourceTabs } from "@/components/dex/PoolSourceTabs";
import { UpstreamNotice } from "@/components/dex/UpstreamNotice";
import { GATEWAY_TAB_CHAIN, type PoolSource } from "@/components/dex/pool-source";
import { useDexUpstream } from "@/hooks/useDexUpstream";
import { useServer } from "@/hooks/useServer";
import { api, type PoolSummary } from "@/lib/api";
import { useDexFavorites } from "@/lib/dexFavorites";

/** GeckoTerminal rate-limits per IP across every viewer and every polling chart. */
const POOL_STALE_MS = 30_000;
const SEARCH_DEBOUNCE_MS = 400;
const PAGE_SIZE = 20;

/** GeckoTerminal's multi-pool endpoint takes at most 30 addresses per request. */
const FAVORITES_BATCH_SIZE = 30;

/**
 * The chain the browser opens on.
 *
 * Solana rather than "whatever Gateway lists first", because it is the only chain
 * with CLMM connectors (Meteora, Orca) behind it — so it is the one chain where a
 * row leads all the way to an open position. The rest of the list comes from
 * `/dex/chains`: Gateway's own networks, intersected with what GeckoTerminal
 * indexes, since a chain missing from either has no pools to browse.
 */
const DEFAULT_NETWORK = "solana-mainnet-beta";

/** Survives a reload, so the chain you browse is not re-picked on every visit. */
const NETWORK_KEY = "condor.dex.network";

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
  const queryClient = useQueryClient();
  const { favorites } = useDexFavorites();
  // Everything on this page comes from one shared GeckoTerminal budget; when it
  // is spent the tables empty out, so the reason is shown above them.
  const upstream = useDexUpstream(server ?? null);
  const [source, setSource] = useState<PoolSource>({
    kind: "gecko",
    view: "trending",
  });
  const [network, setNetwork] = useState<string>(
    () => localStorage.getItem(NETWORK_KEY) || DEFAULT_NETWORK,
  );
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
  // would land the user on an empty table. Chain belongs here too: page 4 of
  // Solana's trending is not page 4 of Base's.
  useEffect(() => {
    setPage(1);
  }, [source, debouncedQuery, dexes, network]);

  useEffect(() => {
    localStorage.setItem(NETWORK_KEY, network);
  }, [network]);

  const { data: chains = [] } = useQuery({
    queryKey: ["dex-chains", server],
    queryFn: () => api.getDexChains(server!),
    enabled: !!server,
    staleTime: 6 * 60 * 60 * 1000,
  });

  // A venue filter is a set of dex ids from the chain that was showing when it was
  // picked, and those ids do not exist on the next one — carried over, it filters
  // every row away and the chain looks empty.
  const onNetworkChange = (next: string) => {
    if (next === network) return;
    setNetwork(next);
    setDexes([]);
    // Meteora and Orca are Solana connectors; their tabs disappear off Solana, and
    // a selection left pointing at a hidden tab shows a table with no active tab.
    if (!next.startsWith(GATEWAY_TAB_CHAIN)) {
      setSource((s) => (s.kind === "gateway" ? { kind: "gecko", view: "trending" } : s));
    }
  };

  // A chain that vanished from Gateway (or was never there — a stale localStorage
  // value from another server) must not strand the browser on a chain the backend
  // will not serve.
  useEffect(() => {
    if (!chains.length) return;
    if (!chains.some((c) => c.network_id === network)) {
      onNetworkChange(chains[0]?.network_id ?? DEFAULT_NETWORK);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chains, network]);

  const { data: venues = [] } = useQuery({
    queryKey: ["dex-venues", server, network],
    queryFn: () => api.getDexVenues(server!, network),
    enabled: !!server,
    staleTime: 6 * 60 * 60 * 1000,
  });

  const isSearch = source.kind === "gecko" && source.view === "token";
  const isAddress = ADDRESS_RE.test(debouncedQuery);
  // The query text only reaches the request for gateway sources and the token
  // search view; keying other views on it would refetch a byte-identical
  // listing (and spend GeckoTerminal budget) when leftover text sits in the
  // box. Same for network/dexes, which gateway requests never send.
  const effectiveQuery =
    source.kind === "gateway" || isSearch ? debouncedQuery : "";
  const isGateway = source.kind === "gateway";
  const enabled =
    !!server &&
    source.kind !== "favorites" &&
    (source.kind === "gateway" || !isSearch || isAddress);

  const {
    data: pagedPools,
    isFetching,
    dataUpdatedAt: poolsUpdatedAt,
  } = useQuery({
    queryKey: [
      "dex-pools",
      server,
      source.kind,
      source.kind === "gecko"
        ? source.view
        : source.kind === "gateway"
          ? source.connector
          : "favorites",
      isGateway ? "" : network,
      effectiveQuery,
      isGateway ? "" : dexes.join(","),
      page,
    ],
    queryFn: () =>
      api.getDexPools(
        server!,
        source.kind === "gateway"
          ? {
              // Orca is read from its own API rather than through Gateway, which
              // proxies it and drops volume, fees, price and yield on the way.
              // Same tab, same columns — filled in rather than em-dashed.
              source: source.connector === "orca" ? "orca" : "gateway",
              connector: source.connector,
              query: effectiveQuery || undefined,
              limit: PAGE_SIZE,
              page,
            }
          : {
              source: "gecko",
              network,
              view: source.kind === "gecko" ? source.view : "trending",
              query: effectiveQuery || undefined,
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
    queryKey: ["dex-pool-by-address", server, network, debouncedQuery],
    queryFn: () =>
      api.getDexPoolByAddress(server!, debouncedQuery, network).catch((e: Error) => {
        // A pasted *token* address 404s here by design — the token search
        // running alongside is the lookup that resolves it — so not-found is a
        // soft null, not an error. Everything else (throttle 503, network)
        // must stay an error: this cache key is shared with DexPool, and a
        // null recorded as success renders its "No pool at …" dead-end there
        // with the Retry button hidden.
        if (/pool not found|request failed: 404/i.test(e.message)) {
          return null as PoolSummary | null;
        }
        throw e;
      }),
    enabled: !!server && isSearch && isAddress,
    staleTime: POOL_STALE_MS,
    retry: false,
  });

  const favoriteAddresses = useMemo(
    () => favorites.filter((f) => f.network === network).map((f) => f.address),
    [favorites, network],
  );

  const {
    data: favoritePage,
    isFetching: favoritesFetching,
    dataUpdatedAt: favoritesUpdatedAt,
  } = useQuery({
    // The address list is deliberately NOT part of the key: un-starring must
    // remove a row the client already has without spending the shared
    // GeckoTerminal budget on a refetch. The queryFn reads the current list
    // from its closure, un-starred rows are filtered out client-side below,
    // and only a *new* address (absent from the cached page) invalidates.
    queryKey: ["dex-favorites", server, network],
    queryFn: async () => {
      // The endpoint takes at most 30 addresses per request, so favorites
      // beyond that are fetched in extra batches instead of silently dropped.
      const batches: string[][] = [];
      for (
        let start = 0;
        start < favoriteAddresses.length;
        start += FAVORITES_BATCH_SIZE
      ) {
        batches.push(
          favoriteAddresses.slice(start, start + FAVORITES_BATCH_SIZE),
        );
      }
      const pages = await Promise.all(
        batches.map((batch) =>
          api.getDexPoolsByAddress(server!, network, batch),
        ),
      );
      return {
        pools: pages.flatMap((page) => page.pools),
        has_more: false,
        // A throttled batch wins the merge: it is the one whose missing rows
        // the empty-state message has to explain, whichever order it landed in.
        upstream:
          pages.find((page) => page.upstream?.throttled_request)?.upstream ??
          pages.at(-1)?.upstream,
      };
    },
    enabled:
      !!server && source.kind === "favorites" && !!favoriteAddresses.length,
    staleTime: POOL_STALE_MS,
  });
  // Un-starring shrinks this list without touching the query, so the row
  // disappears with zero network requests.
  const favoritePools = useMemo(
    () =>
      (favoritePage?.pools ?? []).filter((p) =>
        favoriteAddresses.includes(p.address),
      ),
    [favoritePage, favoriteAddresses],
  );

  // Starring a pool the cached page has never seen is the one favorites change
  // that genuinely needs upstream data. Reading the cache directly (rather
  // than depending on `favoritePage`) keeps a throttled batch that dropped a
  // row from re-triggering this on every response.
  useEffect(() => {
    const cached = queryClient.getQueryData<{ pools: PoolSummary[] }>([
      "dex-favorites",
      server,
      network,
    ]);
    if (!cached) return; // first fetch happens on its own
    const known = new Set(cached.pools.map((p) => p.address));
    if (favoriteAddresses.some((a) => !known.has(a))) {
      queryClient.invalidateQueries({
        queryKey: ["dex-favorites", server, network],
      });
    }
  }, [favoriteAddresses, server, network, queryClient]);

  // A response knows it was throttled before the next poll of /dex/upstream does,
  // and it is the response whose empty table the user is looking at.
  const reportUpstream = upstream.report;
  useEffect(() => {
    reportUpstream(pagedPools?.upstream, poolsUpdatedAt);
  }, [pagedPools, poolsUpdatedAt, reportUpstream]);
  useEffect(() => {
    reportUpstream(favoritePage?.upstream, favoritesUpdatedAt);
  }, [favoritePage, favoritesUpdatedAt, reportUpstream]);

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

  // A throttled fetch and a chain with no pools both arrive as zero rows, and
  // only one of them is worth waiting out — so the table says which it was.
  const throttledPage =
    (isFavorites ? favoritePage?.upstream : pagedPools?.upstream)
      ?.throttled_request ?? false;
  const emptyMessage = isFavorites
    ? throttledPage
      ? "GeckoTerminal is rate limiting Condor — your favorites will reappear in a moment."
      : "No favorites yet — star a pool to keep it here."
    : isSearch && !isAddress
      ? debouncedQuery
        ? "That is not a pool or token address."
        : "Paste a pool or token address to find it."
      : throttledPage || upstream.limited
        ? "GeckoTerminal is rate limiting Condor — no pools could be read. This clears on its own in a few seconds."
        : "No pools found.";

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">DEX</h1>
        <p className="text-sm text-[var(--color-text-muted)]">
          Pools Condor trades through Gateway. Open one to chart it, swap in it
          and provide liquidity to it.
        </p>
      </div>

      {/* Above the browser, because getting back to a pool you are already in is
          a shorter errand than finding a new one — and it renders nothing when
          there is nothing to get back to. */}
      <LpPositions server={server} />

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <UpstreamNotice state={upstream} className="border-b" />

        <PoolSourceTabs
          source={source}
          onSourceChange={setSource}
          chains={chains}
          network={network}
          onNetworkChange={onNetworkChange}
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
                `/dex/${pastedPool.gateway_network || network}/${pastedPool.address}`,
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

        {/* A pasted address that resolved to a pool IS the result: an empty
            token-search table saying "No pools found" under it would deny the
            row right above. */}
        {!(pastedPool && !pools.length) && (
          <PoolBrowser
            pools={pools}
            isLoading={loading}
            emptyMessage={emptyMessage}
            showGatewayColumns={source.kind === "gateway"}
            network={network}
            page={isFavorites ? 1 : page}
            hasMore={isFavorites ? false : !!pagedPools?.has_more}
            onPageChange={setPage}
          />
        )}
      </div>
    </div>
  );
}

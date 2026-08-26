import { QueryClient } from "@tanstack/react-query";

/**
 * App-wide TanStack Query cache.
 *
 * Module-scope singleton (rather than created inside `App`) so that non-React
 * code can reach it — notably the logout path in `lib/auth.ts`, which must drop
 * every cached response before the next user takes over the tab.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
    },
  },
});

/**
 * Granularity (seconds) the candle window bounds are snapped to before they
 * enter a query key. Callers pad their windows by far more than this, so the
 * rounding never changes which candles are usable — it only stops a
 * `Date.now()`-derived bound from minting a new key (and a new request) on
 * every render.
 */
const CANDLE_WINDOW_BUCKET = 300;

/**
 * Cache key + fetch window for a candles request.
 *
 * The window is part of the key on purpose: the same market can be charted over
 * completely different time ranges (two agent sessions, a prefetched default
 * range), and those must not share a cache entry — otherwise the second chart
 * silently renders the first one's candles. Bounds are snapped outward, so the
 * fetched window always covers the requested one.
 *
 * Fetch with the returned `startTime`/`endTime` rather than the raw bounds, so
 * the key always describes the data it holds.
 */
export function candlesQuery(
  server: string,
  connector: string,
  pair: string,
  interval: string,
  start?: number,
  end?: number,
  poolAddress?: string,
) {
  const startTime =
    start === undefined ? undefined : Math.floor(start / CANDLE_WINDOW_BUCKET) * CANDLE_WINDOW_BUCKET;
  const endTime =
    end === undefined ? undefined : Math.ceil(end / CANDLE_WINDOW_BUCKET) * CANDLE_WINDOW_BUCKET;
  return {
    startTime,
    endTime,
    queryKey: [
      "candles",
      server,
      connector,
      pair,
      interval,
      startTime ?? null,
      endTime ?? null,
      // Same market, different pool → different candles.
      poolAddress ?? null,
    ],
  };
}

/**
 * Marks every cache entry belonging to `servers` stale, without fetching any.
 *
 * Server-scoped keys in this app are uniformly `[name, server, …]`, so a switch
 * only has to touch the entries naming the server being left and the one being
 * entered. It used to call bare `invalidateQueries()`: that marked the whole
 * cache stale and immediately refetched every *active* query — and at that
 * instant the active queries are still the OUTGOING server's, their `queryFn`s
 * closed over the previous name. Leaving a slow or offline server therefore
 * fired a burst of portfolio/bots/executors/positions requests *at it*, whose
 * answers were discarded one commit later when `<Outlet key={server}>`
 * remounted the page. It also reset the server-independent entries in passing
 * (`["agents"]`, `["session-options"]` — deliberately `staleTime: Infinity` —
 * `["servers"]`, `["notifications"]`, `["conversations"]`, `["delegations"]`,
 * `["routines"]`, …), forcing a refetch of data no server switch can affect.
 *
 * `refetchType: "none"` is the load-bearing part: stale-marking must never
 * start a request of its own. The incoming server's pages fetch from the
 * remount, and the outgoing server's entries refetch only if the user actually
 * returns to it — which is why they are marked at all rather than left alone,
 * so a return trip shows fresh data instead of a frozen snapshot.
 */
export function invalidateServerScopedQueries(
  client: QueryClient,
  servers: (string | null | undefined)[],
) {
  const names = servers.filter((s): s is string => Boolean(s));
  if (names.length === 0) return;
  client.invalidateQueries({
    predicate: (query) =>
      query.queryKey.some(
        (part) => typeof part === "string" && names.includes(part),
      ),
    refetchType: "none",
  });
}

/**
 * Cache key for an executors list, plus the prefix that matches the whole
 * family on one server.
 *
 * This key is read back positionally by code that did not build it:
 * `lib/shared-socket.ts` pushes each `executors:<server>` frame into every
 * *filtered* entry it finds in the cache, recovering `controllerId` and `pair`
 * from the live keys — the filters exist nowhere else. While those keys were
 * hand-written literals spread over seven files in two different arities, a
 * page that wrote a differently shaped one matched no branch of that bridge and
 * simply stopped receiving updates: a screen that goes quietly stale, with no
 * error anywhere (the shape this repo has already shipped as CORR-006,
 * CORR-180, CORR-185).
 *
 * So the shape is decided here, once, and it is uniform: always four elements,
 * with `""` in a filter slot meaning "not filtered on this". Build keys with
 * this function, read them back with `parseExecutorsKey`, and no other module
 * needs to know the order.
 */
export type ExecutorsQueryKey = [
  root: "executors",
  server: string | null | undefined,
  controllerId: string,
  pair: string,
];

export function executorsQuery(
  server: string | null | undefined,
  opts: { controllerId?: string; pair?: string } = {},
) {
  return {
    queryKey: [
      "executors",
      server,
      opts.controllerId ?? "",
      opts.pair ?? "",
    ] as ExecutorsQueryKey,
    /**
     * Every executors entry for this server, filtered or not — what a mutation
     * invalidates when it cannot know which narrowings are currently mounted.
     */
    prefix: ["executors", server] as [string, string | null | undefined],
  };
}

/**
 * Recover the filters from an executors key, or `null` if the key is not one.
 *
 * Deliberately strict: a cache scan reaches this with arbitrary keys, and the
 * only keys that can be shaped like an executors key are the ones
 * `executorsQuery` built.
 */
export function parseExecutorsKey(
  key: readonly unknown[],
): { server: string; controllerId: string; pair: string } | null {
  if (key.length !== 4) return null;
  const [root, server, controllerId, pair] = key;
  if (root !== "executors") return null;
  if (
    typeof server !== "string" ||
    typeof controllerId !== "string" ||
    typeof pair !== "string"
  ) {
    return null;
  }
  return { server, controllerId, pair };
}

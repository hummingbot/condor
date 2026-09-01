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

/**
 * Cache keys for the controller performance histories, plus the prefixes the
 * shared socket routes live frames by.
 *
 * Like the executors key above, these are read back positionally by code that
 * did not build them: `lib/shared-socket.ts` merges each `controller_perf`
 * frame into every cached entry it finds under a *prefix* of these keys, and
 * re-checks both roots when the connection comes back. It cannot reconstruct a
 * whole key — the tail is a time bound and a sampling interval derived from
 * data the socket never sees — so the routing depends entirely on the leading
 * elements being where it expects them.
 *
 * Three constraints hold that bridge together, and they live here rather than
 * as prose repeated at each site:
 *
 *   - `bot_name` at index 2, `controller_id` at index 3. The per-controller
 *     query asks upstream for one bot's rows, so the routing has to be scoped
 *     to one bot too; grouping on the bare controller id pushed a sibling
 *     bot's rows into its neighbour's chart (CORR-241).
 *   - The sampling interval goes **last**. It is part of the identity so a
 *     coarse and a fine series never share an entry (PERF-238), but the socket
 *     matches by prefix, so it can only be appended — an interval one slot
 *     earlier silently ends live updates.
 *   - The two roots are separate keys, never one prefix of the other:
 *     react-query matches element by element, so `"controller-perf-history"`
 *     does not match `"controller-perf-history-all"`.
 *
 * This family has already shipped that failure twice — CORR-224 (the socket
 * wrote a shorter key than the readers used, and `setQueryData` matches the
 * hash exactly, so every frame was discarded for weeks) and CORR-241 — which
 * is why the shape is decided here, once.
 */
export type ControllerPerfHistoryKey = [
  root: "controller-perf-history",
  server: string | null | undefined,
  botName: string,
  controllerId: string,
  start: string | null | undefined,
  interval: string | undefined,
];

export type ControllerPerfHistoryAllKey = [
  root: "controller-perf-history-all",
  server: string | null | undefined,
  start: string | null | undefined,
  interval: string | undefined,
];

/**
 * One controller's history on one bot.
 *
 * `start` and `interval` are optional so the socket can build the identity
 * half without knowing the window: it reads `prefix` and leaves `queryKey` to
 * the component that owns the fetch.
 */
export function controllerPerfHistoryQuery(
  server: string | null | undefined,
  opts: {
    botName: string;
    controllerId: string;
    start?: string | null;
    interval?: string;
  },
) {
  return {
    queryKey: [
      "controller-perf-history",
      server,
      opts.botName,
      opts.controllerId,
      opts.start,
      opts.interval,
    ] as ControllerPerfHistoryKey,
    /** Every window and resolution cached for this bot's controller. */
    prefix: ["controller-perf-history", server, opts.botName, opts.controllerId] as [
      string,
      string | null | undefined,
      string,
      string,
    ],
  };
}

/** The whole fleet's history on one server, in a single entry. */
export function controllerPerfHistoryAllQuery(
  server: string | null | undefined,
  opts: { start?: string | null; interval?: string } = {},
) {
  return {
    queryKey: [
      "controller-perf-history-all",
      server,
      opts.start,
      opts.interval,
    ] as ControllerPerfHistoryAllKey,
    /** Every window and resolution cached for this server's fleet. */
    prefix: ["controller-perf-history-all", server] as [
      string,
      string | null | undefined,
    ],
  };
}

/**
 * Both roots, for the reconnect re-check that cannot know which windows are
 * mounted. Listed separately because react-query matches element by element.
 */
export const CONTROLLER_PERF_ROOTS = [
  "controller-perf-history-all",
  "controller-perf-history",
] as const;

/**
 * Recover a per-controller history key, or `null` if the key is not one.
 *
 * The inverse of `controllerPerfHistoryQuery`, and the reason a reorder cannot
 * pass unnoticed: the round trip is asserted in `queryClient.test.ts`, so
 * moving `bot_name` off index 2 or the interval off the end fails a test
 * rather than quietly unplugging the socket.
 */
export function parseControllerPerfHistoryKey(
  key: readonly unknown[],
): {
  server: string;
  botName: string;
  controllerId: string;
  start: string | null | undefined;
  interval: string | undefined;
} | null {
  if (key.length !== 6) return null;
  const [root, server, botName, controllerId, start, interval] = key;
  if (root !== "controller-perf-history") return null;
  if (
    typeof server !== "string" ||
    typeof botName !== "string" ||
    typeof controllerId !== "string"
  ) {
    return null;
  }
  if (start != null && typeof start !== "string") return null;
  if (interval !== undefined && typeof interval !== "string") return null;
  return { server, botName, controllerId, start: start as string | null | undefined, interval };
}

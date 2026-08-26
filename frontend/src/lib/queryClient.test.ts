/**
 * Guards the server switch (PERF-223).
 *
 * The switch used to call bare `invalidateQueries()`, which refetched every
 * *active* query at that instant — and those queries still belonged to the
 * server being left, so switching away from a slow or offline server fired a
 * burst of requests at it. The two properties that fix depends on are checked
 * here directly: an active query on the outgoing server must be marked stale
 * but must NOT refetch, and entries that no server switch can affect must not
 * be touched at all.
 *
 * Cache in, cache out: no DOM, no React, so this runs under vitest's `node`
 * environment (see vite.config.ts).
 */

import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  executorsQuery,
  invalidateServerScopedQueries,
  parseExecutorsKey,
} from "@/lib/queryClient";

const PREVIOUS = "server-a";
const NEXT = "server-b";

let client: QueryClient;

/** Every key shape a server switch must leave completely alone. */
const SERVER_INDEPENDENT = [
  ["agents"],
  ["session-options"],
  ["servers"],
  ["notifications"],
  ["conversations"],
  ["delegations"],
  ["routines"],
  ["settings-servers"],
];

/** The server-scoped shapes actually used across the app. */
const scopedKeys = (server: string) => [
  ["portfolio", server],
  ["bots", server],
  executorsQuery(server).queryKey,
  executorsQuery(server, { controllerId: "main", pair: "BTC-USDT" }).queryKey,
  ["consolidated-positions", server],
  ["settings-credentials", server],
  ["settings-connectors", server, "spot"],
  ["trading-rules", server, "binance_perpetual"],
  ["candles", server, "binance_perpetual", "BTC-USDT", "5m", null, null, null],
];

const isInvalidated = (key: unknown[]) =>
  client.getQueryState(key)?.isInvalidated === true;

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
});

afterEach(() => {
  client.clear();
});

describe("invalidateServerScopedQueries", () => {
  it("marks the outgoing and incoming servers' entries stale", () => {
    for (const key of [...scopedKeys(PREVIOUS), ...scopedKeys(NEXT)]) {
      client.setQueryData(key, "cached");
    }

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);

    for (const key of [...scopedKeys(PREVIOUS), ...scopedKeys(NEXT)]) {
      expect(isInvalidated(key), JSON.stringify(key)).toBe(true);
    }
  });

  it("leaves server-independent entries untouched", () => {
    for (const key of SERVER_INDEPENDENT) client.setQueryData(key, "cached");
    for (const key of scopedKeys(PREVIOUS)) client.setQueryData(key, "cached");

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);

    for (const key of SERVER_INDEPENDENT) {
      expect(isInvalidated(key), JSON.stringify(key)).toBe(false);
      expect(client.getQueryData(key)).toBe("cached");
    }
  });

  it("leaves a third server's entries untouched", () => {
    for (const key of scopedKeys("server-c")) client.setQueryData(key, "cached");

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);

    for (const key of scopedKeys("server-c")) {
      expect(isInvalidated(key), JSON.stringify(key)).toBe(false);
    }
  });

  it("does not refetch an active query on the server being left", async () => {
    const queryFn = vi.fn().mockResolvedValue("portfolio");
    const observer = new QueryObserver(client, {
      queryKey: ["portfolio", PREVIOUS],
      queryFn,
    });
    const unsubscribe = observer.subscribe(() => {});
    // Let the first fetch *settle*: a success landing after the invalidation
    // would clear the flag again, and the assertion below would be measuring
    // the race rather than the behaviour.
    await vi.waitFor(() =>
      expect(observer.getCurrentResult().isSuccess).toBe(true),
    );
    expect(queryFn).toHaveBeenCalledTimes(1);

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);
    await new Promise((resolve) => setTimeout(resolve, 20));

    // Stale-marked so a return trip refetches — but not fetched from here,
    // which is the whole point: the request would have gone to `PREVIOUS`.
    expect(isInvalidated(["portfolio", PREVIOUS])).toBe(true);
    expect(queryFn).toHaveBeenCalledTimes(1);

    unsubscribe();
  });

  it("does not refetch an active server-independent query", async () => {
    const queryFn = vi.fn().mockResolvedValue(["opt"]);
    const observer = new QueryObserver(client, {
      queryKey: ["session-options"],
      queryFn,
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() =>
      expect(observer.getCurrentResult().isSuccess).toBe(true),
    );
    expect(queryFn).toHaveBeenCalledTimes(1);

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(isInvalidated(["session-options"])).toBe(false);
    expect(queryFn).toHaveBeenCalledTimes(1);

    unsubscribe();
  });

  it("is a no-op on the first selection, when there is no previous server", () => {
    for (const key of SERVER_INDEPENDENT) client.setQueryData(key, "cached");

    invalidateServerScopedQueries(client, [null, undefined]);

    for (const key of SERVER_INDEPENDENT) {
      expect(isInvalidated(key), JSON.stringify(key)).toBe(false);
    }
  });
});

/**
 * Pins the executors key contract (ARCH-227).
 *
 * `lib/shared-socket.ts` recovers the filters from whatever executors keys it
 * finds in the cache, so a key the factory builds and the parser then fails to
 * recognise is not a type error — it is a screen that stops receiving live
 * updates and says nothing. Every shape the app builds is asserted to survive
 * the round trip, to be reachable from the invalidation prefix, and to be seen
 * by the server switch.
 */
describe("executorsQuery / parseExecutorsKey", () => {
  const SHAPES = [
    ["unfiltered", {}, { controllerId: "", pair: "" }],
    ["controller only", { controllerId: "main" }, { controllerId: "main", pair: "" }],
    ["pair only", { pair: "BTC-USDT" }, { controllerId: "", pair: "BTC-USDT" }],
    [
      "controller + pair",
      { controllerId: "main", pair: "SOL-USDC" },
      { controllerId: "main", pair: "SOL-USDC" },
    ],
  ] as const;

  it.each(SHAPES)("round-trips the %s key", (_label, opts, expected) => {
    expect(parseExecutorsKey(executorsQuery(PREVIOUS, opts).queryKey)).toEqual({
      server: PREVIOUS,
      ...expected,
    });
  });

  it("gives every shape the same arity, so no reader has to branch on length", () => {
    for (const [, opts] of SHAPES) {
      expect(executorsQuery(PREVIOUS, opts).queryKey).toHaveLength(4);
    }
  });

  it("finds every shape from the invalidation prefix", () => {
    for (const [, opts] of SHAPES) {
      client.setQueryData(executorsQuery(PREVIOUS, opts).queryKey, []);
    }
    // A neighbouring family that must NOT be swept up by the prefix.
    client.setQueryData(["executors-infinite", PREVIOUS], []);

    const found = client
      .getQueryCache()
      .findAll({ queryKey: executorsQuery(PREVIOUS).prefix });

    expect(found).toHaveLength(SHAPES.length);
    for (const entry of found) {
      expect(parseExecutorsKey(entry.queryKey)?.server).toBe(PREVIOUS);
    }
  });

  it("does not match another server's entries", () => {
    client.setQueryData(executorsQuery(NEXT, { pair: "BTC-USDT" }).queryKey, []);
    expect(
      client.getQueryCache().findAll({ queryKey: executorsQuery(PREVIOUS).prefix }),
    ).toHaveLength(0);
  });

  it("rejects keys that are not executors keys", () => {
    expect(parseExecutorsKey(["executors-infinite", PREVIOUS])).toBeNull();
    expect(parseExecutorsKey(["portfolio", PREVIOUS])).toBeNull();
    // The pre-ARCH-227 three-element shapes, which no longer exist.
    expect(parseExecutorsKey(["executors", PREVIOUS, ""])).toBeNull();
    expect(parseExecutorsKey(["executors", PREVIOUS, "main"])).toBeNull();
    // A key minted before a server was picked.
    expect(parseExecutorsKey(executorsQuery(null).queryKey)).toBeNull();
  });

  it("is reached by the server switch", () => {
    const key = executorsQuery(PREVIOUS, { controllerId: "main", pair: "SOL-USDC" })
      .queryKey;
    client.setQueryData(key, "cached");

    invalidateServerScopedQueries(client, [PREVIOUS, NEXT]);

    expect(isInvalidated(key)).toBe(true);
  });
});

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
  CONTROLLER_PERF_ROOTS,
  controllerPerfHistoryAllQuery,
  controllerPerfHistoryQuery,
  executorsQuery,
  invalidateServerScopedQueries,
  parseControllerPerfHistoryKey,
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

/**
 * Pins the controller performance-history key shape (ARCH-285).
 *
 * `lib/shared-socket.ts` routes every live `controller_perf` frame into these
 * entries by a *prefix* of the key, so the shape is a contract between modules
 * that never call each other. Breaking it does not throw: the charts simply
 * stop updating, which this repo has already shipped twice (CORR-224,
 * CORR-241). These tests are what makes a reorder fail loudly instead.
 */
describe("controller performance-history keys", () => {
  const BOT = "epsilon";
  const CTRL = "pmm_1";
  const START = "2026-08-01T00:00:00.000Z";
  const INTERVAL = "1h";

  /** What the socket does: does this prefix still find the entry? */
  const findAll = (prefix: unknown[]) =>
    client.getQueryCache().findAll({ queryKey: prefix });

  it("round-trips a per-controller key through its parser", () => {
    const { queryKey } = controllerPerfHistoryQuery(PREVIOUS, {
      botName: BOT,
      controllerId: CTRL,
      start: START,
      interval: INTERVAL,
    });

    expect(parseControllerPerfHistoryKey(queryKey)).toEqual({
      server: PREVIOUS,
      botName: BOT,
      controllerId: CTRL,
      start: START,
      interval: INTERVAL,
    });
  });

  it("keeps bot_name at index 2, controller_id at index 3 and the interval last", () => {
    const { queryKey } = controllerPerfHistoryQuery(PREVIOUS, {
      botName: BOT,
      controllerId: CTRL,
      start: START,
      interval: INTERVAL,
    });

    // Spelled out positionally on purpose: the socket reads these slots, and a
    // structural assertion would still pass if two of them swapped.
    expect(queryKey[2]).toBe(BOT);
    expect(queryKey[3]).toBe(CTRL);
    expect(queryKey[queryKey.length - 1]).toBe(INTERVAL);
  });

  it("builds a prefix that is an element-wise prefix of the full key", () => {
    const single = controllerPerfHistoryQuery(PREVIOUS, {
      botName: BOT,
      controllerId: CTRL,
      start: START,
      interval: INTERVAL,
    });
    const fleet = controllerPerfHistoryAllQuery(PREVIOUS, {
      start: START,
      interval: INTERVAL,
    });

    expect(single.queryKey.slice(0, single.prefix.length)).toEqual(single.prefix);
    expect(fleet.queryKey.slice(0, fleet.prefix.length)).toEqual(fleet.prefix);
  });

  it("is still found by the prefix the socket builds without the window", () => {
    const single = controllerPerfHistoryQuery(PREVIOUS, {
      botName: BOT,
      controllerId: CTRL,
      start: START,
      interval: INTERVAL,
    }).queryKey;
    const fleet = controllerPerfHistoryAllQuery(PREVIOUS, {
      start: START,
      interval: INTERVAL,
    }).queryKey;
    client.setQueryData(single, { snapshots: [] });
    client.setQueryData(fleet, { snapshots: [] });

    // The socket knows the identity but neither the time bound nor the interval.
    expect(
      findAll(controllerPerfHistoryQuery(PREVIOUS, { botName: BOT, controllerId: CTRL }).prefix),
    ).toHaveLength(1);
    expect(findAll(controllerPerfHistoryAllQuery(PREVIOUS).prefix)).toHaveLength(1);
  });

  it("does not route one bot's frames into another bot's chart", () => {
    client.setQueryData(
      controllerPerfHistoryQuery(PREVIOUS, {
        botName: "alpha",
        controllerId: CTRL,
        start: START,
        interval: INTERVAL,
      }).queryKey,
      { snapshots: [] },
    );

    expect(
      findAll(
        controllerPerfHistoryQuery(PREVIOUS, { botName: "beta", controllerId: CTRL }).prefix,
      ),
    ).toHaveLength(0);
    expect(
      findAll(controllerPerfHistoryQuery(NEXT, { botName: "alpha", controllerId: CTRL }).prefix),
    ).toHaveLength(0);
  });

  it("keeps the two roots apart, since react-query matches element by element", () => {
    client.setQueryData(
      controllerPerfHistoryAllQuery(PREVIOUS, { start: START, interval: INTERVAL }).queryKey,
      { snapshots: [] },
    );

    expect(CONTROLLER_PERF_ROOTS).toEqual([
      "controller-perf-history-all",
      "controller-perf-history",
    ]);
    // The fleet entry must not answer to the per-controller root.
    expect(findAll(["controller-perf-history"])).toHaveLength(0);
    expect(findAll(["controller-perf-history-all"])).toHaveLength(1);
  });

  it("rejects keys that are not per-controller history keys", () => {
    expect(
      parseControllerPerfHistoryKey(
        controllerPerfHistoryAllQuery(PREVIOUS, { start: START, interval: INTERVAL }).queryKey,
      ),
    ).toBeNull();
    // The pre-CORR-241 shape, with no bot at index 2.
    expect(
      parseControllerPerfHistoryKey(["controller-perf-history", PREVIOUS, CTRL, START, INTERVAL]),
    ).toBeNull();
    expect(parseControllerPerfHistoryKey(executorsQuery(PREVIOUS).queryKey)).toBeNull();
  });
});

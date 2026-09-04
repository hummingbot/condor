/**
 * Process-wide `CondorWebSocket`, shared by every `useCondorWebSocket` caller.
 *
 * Each hook instance used to construct its own `CondorWebSocket`, so a page
 * with three subscribers (`/trade`: CreateExecutor + OrderBook + RecentTrades)
 * opened three connections to `/api/v1/ws`. Nothing about the wire protocol
 * needs that: the backend routes purely by channel name — the target server is
 * the channel's second segment and access is authorized per `subscribe` — so
 * one connection can serve every consumer, and every server, at once.
 *
 * Two independent reference counts live here:
 *
 *   - `socketRefs`  — how many hooks currently want a connection at all.
 *   - `channelRefs` — how many hooks currently want each channel. This one is
 *     not optional: server-side `conn.channels` is a *set*, so a single
 *     consumer unsubscribing would silence a channel that other mounted
 *     consumers still depend on.
 *
 * Candle channels are deliberately absent from `channelRefs`. `candle-store.ts`
 * owns those (it has its own refcount and deferred teardown) and talks to this
 * socket directly once `attachWs` hands it over.
 */

import { candleStore } from "./candle-store";
import { controllerKey } from "./controller-identity";
import {
  CONTROLLER_PERF_ROOTS,
  controllerPerfHistoryAllQuery,
  controllerPerfHistoryQuery,
  executorsQuery,
  parseExecutorsKey,
  queryClient,
} from "./queryClient";
import type {
  BotsPageResponse,
  ControllerInfo,
  ControllerPerformanceHistoryResponse,
  ControllerPerformanceSnapshot,
} from "./api";
import { CondorWebSocket } from "./websocket";

/**
 * Grace period before a socket with no consumers is actually closed.
 *
 * React unmounts the outgoing page's consumers before mounting the incoming
 * page's, so on every route change the count legitimately touches zero for one
 * commit. Closing synchronously would drop and redial the connection on each
 * navigation (and on every StrictMode remount in dev). The delay only has to
 * outlive a commit, so it stays short enough that a real teardown — logout,
 * closing the last consumer — still severs the connection promptly.
 */
const TEARDOWN_DELAY_MS = 250;

let socket: CondorWebSocket | null = null;
let socketToken: string | null = null;
let socketRefs = 0;
let teardownTimer: ReturnType<typeof setTimeout> | null = null;
/** Bumped on every (re)connect so hooks can re-attach their own listeners. */
let version = 0;

const channelRefs = new Map<string, number>();
const connectHandlers = new Set<() => void>();

// ── Cache writes ──

/**
 * Merge incoming performance snapshots into existing ones, deduplicating by
 * controller *and bot* plus timestamp.
 *
 * The bot is not decoration in this key: `controller_id` is a config id two
 * bots can be running at once, and the sampler dumps them at one shared
 * timestamp, so deduping on `id:timestamp` threw away the second bot's frame
 * every tick as if it were a repeat of the first (CORR-241).
 */
function mergeSnapshots(
  existing: ControllerPerformanceSnapshot[],
  incoming: ControllerPerformanceSnapshot[],
): ControllerPerformanceSnapshot[] {
  const merged = [...existing];
  const snapKey = (s: ControllerPerformanceSnapshot) => `${controllerKey(s)}:${s.timestamp}`;
  const seen = new Set(existing.map(snapKey));
  for (const snap of incoming) {
    const key = snapKey(snap);
    if (!seen.has(key)) {
      merged.push(snap);
      seen.add(key);
    }
  }
  return merged;
}

/**
 * Merge snapshots into every cached performance-history query under `prefix`.
 *
 * The readers of these caches append a time bound and a sampling interval to
 * the key (see `controllerPerfHistoryQuery` for the whole shape) that is
 * derived from data this module never sees. `setQueryData` matches by
 * exact key hash, so the socket has to discover the live keys rather than
 * reconstruct them. Entries that don't exist yet are left alone — the query's own fetch
 * seeds them, and merging into a missing entry would produce a history with no
 * beginning.
 */
function mergeIntoMatchingQueries(
  prefix: unknown[],
  snapshots: ControllerPerformanceSnapshot[],
): void {
  for (const entry of queryClient.getQueryCache().findAll({ queryKey: prefix })) {
    queryClient.setQueryData(
      entry.queryKey,
      (old: ControllerPerformanceHistoryResponse | undefined) => {
        if (!old) return old;
        // Append new snapshots and deduplicate by bot+controller+timestamp
        return { ...old, snapshots: mergeSnapshots(old.snapshots ?? [], snapshots) };
      },
    );
  }
}

/**
 * The rows an `executors:<server>` frame carries.
 *
 * The stream is untyped on the wire, so only the fields this module reads are
 * asserted: the `id` the merge keys on, and the two columns the filtered views
 * narrow by.
 */
type ExecutorFrameRow = {
  id?: string;
  controller_id?: string;
  trading_pair?: string;
};

/** The `["executors-infinite", server]` cache, as the walk in `Bots.tsx` builds it. */
type ExecutorPages = {
  pages?: { executors?: ExecutorFrameRow[]; next_cursor?: string | null }[];
  pageParams?: unknown[];
};

/**
 * Fold a live `executors` frame into the cursor-paginated infinite list.
 *
 * Pages 1..n of that query are anchored on the cursor page 0 ended at
 * (`getNextPageParam: lastPage.next_cursor`), so the seam between two pages
 * never moves once it is drawn. Rewriting page 0 by position — what this used
 * to do, `frame.slice(0, page0.length)` — therefore slid page 0's window
 * without sliding the seam: every row the frame pushed past the end of page 0
 * was then in no page at all. It disappeared from the table and, worse, from
 * the KPI totals folded over those same flattened pages, until the 60s
 * fallback poll happened to repair it. A frame *shorter* than page 0
 * truncated it outright, which the stream does routinely: it broadcasts the
 * in-memory executor list, a different, generally smaller and differently
 * ordered set than the REST history the walk paged through.
 *
 * So merge by id instead — the same upsert `useMainControllerData` folds its
 * three sources with. Every held row the frame also carries is refreshed where
 * it already sits; only ids no page holds are prepended to page 0; nothing is
 * ever dropped for being absent from the frame, because the frame says what is
 * live now, not what the history contains. A row the walk handed out twice
 * (an active executor can be served both from memory and from its DB page) is
 * collapsed to its first copy, since a duplicate is counted twice by every
 * total on the screen.
 */
function mergeExecutorPages(
  old: ExecutorPages | undefined,
  incoming: ExecutorFrameRow[],
): ExecutorPages | undefined {
  if (!old?.pages?.length) return old;

  const fresh = new Map<string, ExecutorFrameRow>();
  for (const ex of incoming) {
    if (ex?.id) fresh.set(ex.id, ex);
  }

  const held = new Set<string>();
  let touched = false;
  const pages = old.pages.map((page) => {
    let changed = false;
    const executors: ExecutorFrameRow[] = [];
    for (const row of page.executors ?? []) {
      const id = row?.id;
      if (!id) {
        executors.push(row);
        continue;
      }
      if (held.has(id)) {
        changed = true; // already kept, in this page or an earlier one
        continue;
      }
      held.add(id);
      const next = fresh.get(id);
      if (next && next !== row) changed = true;
      executors.push(next ?? row);
    }
    if (!changed) return page;
    touched = true;
    return { ...page, executors };
  });

  // Whatever the pages did not already hold is genuinely new, and belongs at
  // the head of the newest page.
  const added = [...fresh.entries()].filter(([id]) => !held.has(id)).map(([, ex]) => ex);
  if (!added.length) return touched ? { ...old, pages } : old;

  const [first, ...rest] = pages;
  return {
    ...old,
    pages: [{ ...first, executors: [...added, ...(first.executors ?? [])] }, ...rest],
  };
}

/**
 * Whether this socket has ever been up, so the first connect is not read as a gap.
 */
let everConnected = false;

/**
 * Re-check the performance histories after the connection came back.
 *
 * While the socket is up, `mergeIntoMatchingQueries` keeps these entries at the
 * live edge and the queries' own timer is only a long safety net
 * (`HISTORY_REFETCH_MS`). A drop breaks the first half of that: frames sent
 * while the connection was down are gone — the stream broadcasts the *latest*
 * snapshot every 30s and never replays — so the cached series has a hole
 * between the disconnect and now, and waiting up to ten minutes for the net to
 * catch it would be exactly the "quietly stale to save requests" trade this
 * whole change exists not to make.
 *
 * Invalidating instead makes the repair immediate and still cheap: each active
 * chart refetches, and a refetch is now a tail fetch from its own newest cached
 * snapshot (`refreshControllerHistory`), so the request that closes the gap is
 * bounded by the length of the gap rather than by the length of the history.
 * That is what makes an aggressive repair affordable here.
 *
 * The first connect of a socket is skipped: nothing was missed before there was
 * a connection, and the queries are loading their first full walk at that exact
 * moment. Both roots are re-checked separately (`CONTROLLER_PERF_ROOTS`)
 * because react-query matches key arrays element by element.
 */
function resyncPerformanceHistories(): void {
  if (!everConnected) {
    everConnected = true;
    return;
  }
  for (const root of CONTROLLER_PERF_ROOTS) {
    queryClient.invalidateQueries({ queryKey: [root] });
  }
}

/**
 * The single message handler for the shared socket.
 *
 * Registered once per connection at module scope, so it cannot close over any
 * one hook's `server` prop — several consumers may be watching different
 * servers over this connection. Every channel encodes its server as the second
 * segment (`portfolio:<server>`, `orderbook:<server>:<connector>:<pair>`, …),
 * which is what the backend authorizes against, so that is the authoritative
 * source for the cache key too.
 *
 * Exported for tests; the socket wires it up itself in `openSocket`.
 */
export function handleMessage(channel: string, data: unknown): void {
  const parts = channel.split(":");
  const prefix = parts[0];
  const server = parts[1];

  // Candle data is managed by candle-store.ts — only update status here
  if (prefix === "candles") {
    if (parts.length >= 5) {
      const [, srv, conn, pr, iv] = parts;
      const payload = data as { type: string; message?: string };
      if (payload.type === "error") {
        queryClient.setQueryData(
          ["candles-status", srv, conn, pr, iv],
          { status: "error", message: payload.message ?? "Unknown error" },
        );
      } else if (payload.type === "candle_update" || payload.type === "candles") {
        queryClient.setQueryData(
          ["candles-status", srv, conn, pr, iv],
          { status: "connected" },
        );
      }
    }
    return;
  }

  if (!server) return;

  if (prefix === "portfolio") {
    queryClient.setQueryData(["portfolio", server], data);
  } else if (prefix === "bots") {
    queryClient.setQueryData(["bots", server], (old: BotsPageResponse | undefined) => {
      const incoming = data as BotsPageResponse;
      if (!incoming?.controllers) return old ?? data;
      if (!old?.controllers?.length) return incoming;

      // Key by bot + controller_id (stable) — controller_name may differ
      // between REST and WS, and the id alone is shared by every bot running
      // the same controller config (CORR-241).
      const oldMap = new Map<string, ControllerInfo>();
      for (const c of old.controllers) {
        oldMap.set(controllerKey(c), c);
      }
      const oldBotMap = new Map(old.bots.map((b) => [b.bot_name, b]));

      return {
        ...incoming,
        controllers: incoming.controllers.map((c) => {
          const prev = oldMap.get(controllerKey(c));
          if (!prev) return c;
          return {
            ...c,
            config: Object.keys(c.config || {}).length ? c.config : prev.config,
            deployed_at: c.deployed_at ?? prev.deployed_at,
            connector: c.connector || prev.connector,
            trading_pair: c.trading_pair || prev.trading_pair,
            controller_name: prev.controller_name || c.controller_name,
            controller_id: prev.controller_id || c.controller_id,
          };
        }),
        bots: incoming.bots.map((b) => {
          const prev = oldBotMap.get(b.bot_name);
          return { ...b, deployed_at: b.deployed_at ?? prev?.deployed_at ?? null };
        }),
      };
    });
  } else if (prefix === "executors") {
    const unfiltered = executorsQuery(server);
    queryClient.setQueryData(unfiltered.queryKey, data);
    const allExecs = data as ExecutorFrameRow[];
    if (Array.isArray(allExecs)) {
      // Filtered views of the same list are derived from this one frame. Which
      // narrowings someone is currently watching is recorded nowhere but in the
      // live keys, so they are read back through the shared parser rather than
      // destructured here — `executorsQuery` owns the order.
      for (const entry of queryClient.getQueryCache().findAll({ queryKey: unfiltered.prefix })) {
        const filter = parseExecutorsKey(entry.queryKey);
        if (!filter) continue;
        // The unfiltered entry, already written above.
        if (!filter.controllerId && !filter.pair) continue;
        queryClient.setQueryData(
          entry.queryKey,
          allExecs.filter(
            (ex) =>
              (!filter.controllerId || ex.controller_id === filter.controllerId) &&
              (!filter.pair || ex.trading_pair === filter.pair),
          ),
        );
      }

      // The paginated list the browser walks reads the same frame, but it can
      // only be merged into — see `mergeExecutorPages`.
      queryClient.setQueryData(
        ["executors-infinite", server],
        (old: ExecutorPages | undefined) => mergeExecutorPages(old, allExecs),
      );
    }
  } else if (prefix === "controller_perf") {
    const incoming = data as { snapshots?: ControllerPerformanceSnapshot[] };
    if (incoming?.snapshots) {
      // Both readers key on a time bound the socket cannot know — the fleet
      // query adds `earliestDeploy` (ActiveBotsTab) and the per-controller one
      // adds `deployedAt` (ControllerPnlChart) — and on the sampling interval
      // derived from it (PERF-238). `setQueryData` matches the key hash
      // exactly, so writing the short prefix here landed on an entry that never
      // exists and every frame was silently dropped. Resolve the live keys from
      // the cache instead, the way the `executors` branch above does. Live
      // frames arrive at the socket's own cadence and are merged whatever the
      // cache's interval: they only add detail at the right-hand edge.
      mergeIntoMatchingQueries(
        controllerPerfHistoryAllQuery(server).prefix,
        incoming.snapshots,
      );

      // Same, per controller. The per-controller cache is scoped to one bot
      // (ControllerPnlChart asks upstream for a single `bot_name`), so the
      // routing has to be scoped to one bot too: grouping on the bare
      // `controller_id` pushed a sibling bot's rows into its neighbour's chart
      // whenever both ran the same controller config (CORR-241).
      const byController = new Map<string, ControllerPerformanceSnapshot[]>();
      for (const snap of incoming.snapshots) {
        const cid = snap.controller_id || snap.controller_name;
        if (!cid) continue;
        const key = `${snap.bot_name ?? ""}\u0000${cid}`;
        const arr = byController.get(key) ?? [];
        arr.push(snap);
        byController.set(key, arr);
      }
      for (const [key, snaps] of byController) {
        const [botName, cid] = key.split("\u0000");
        mergeIntoMatchingQueries(
          controllerPerfHistoryQuery(server, { botName, controllerId: cid }).prefix,
          snaps,
        );
      }
    }
  } else if (prefix === "orderbook") {
    if (parts.length >= 4) {
      const [, srv, connector, pair] = parts;
      queryClient.setQueryData(["order-book", srv, connector, pair], data);
    }
  }
}

// ── Socket lifecycle ──

function openSocket(token: string): CondorWebSocket {
  const ws = new CondorWebSocket(token);
  socket = ws;
  socketToken = token;

  ws.onMessage(handleMessage);
  ws.onConnect(() => {
    version++;
    resyncPerformanceHistories();
    for (const handler of connectHandlers) handler();
  });

  // The candle store reads candle frames straight off this socket.
  candleStore.attachWs(ws);
  ws.connect();

  // Replay what consumers already asked for. Channels can be registered while
  // no socket exists (mounted before login, or between the close and reopen of
  // a token change); `subscribe` also records them on the socket itself, so the
  // `onopen` handler re-sends anything queued here before the connection came up.
  for (const [channel, refs] of channelRefs) {
    if (refs > 0) ws.subscribe(channel);
  }

  return ws;
}

function closeSocket(): void {
  if (!socket) return;
  candleStore.detachWs(socket);
  socket.disconnect();
  socket = null;
  socketToken = null;
}

/**
 * Take a reference on the shared socket, opening it if needed.
 *
 * A token that differs from the live socket's is a different session: the old
 * connection is authenticated as the previous user and must not survive into
 * the new one, so it is closed and replaced rather than reused.
 */
export function acquireSocket(token: string): CondorWebSocket {
  if (teardownTimer !== null) {
    clearTimeout(teardownTimer);
    teardownTimer = null;
  }
  if (socket && socketToken !== token) closeSocket();
  socketRefs++;
  return socket ?? openSocket(token);
}

/**
 * State of the shared connection, for a bug report to quote.
 *
 * "closed" while the dashboard is open is itself the bug in most stale-data
 * reports, and it is not something a user can see.
 */
export function socketStatus(): "connected" | "connecting" | "closed" {
  if (!socket) return "closed";
  return socket.isOpen ? "connected" : "connecting";
}

/** Drop a reference. The last one closes the socket after a short grace period. */
export function releaseSocket(): void {
  socketRefs = Math.max(0, socketRefs - 1);
  if (socketRefs > 0) return;
  if (teardownTimer !== null) clearTimeout(teardownTimer);
  teardownTimer = setTimeout(() => {
    teardownTimer = null;
    if (socketRefs === 0) closeSocket();
  }, TEARDOWN_DELAY_MS);
}

// ── Channel lifecycle ──

/** Reference-counted subscribe. Only the first reference hits the wire. */
export function subscribeChannel(channel: string): void {
  const refs = (channelRefs.get(channel) ?? 0) + 1;
  channelRefs.set(channel, refs);
  if (refs === 1) socket?.subscribe(channel);
}

/** Reference-counted unsubscribe. Only the last reference hits the wire. */
export function unsubscribeChannel(channel: string): void {
  const refs = channelRefs.get(channel);
  if (!refs) return;
  if (refs > 1) {
    channelRefs.set(channel, refs - 1);
    return;
  }
  channelRefs.delete(channel);
  socket?.unsubscribe(channel);
}

// ── Connect notifications ──

/** Subscribe to (re)connect events. Returns an unregister function. */
export function onSocketConnect(handler: () => void): () => void {
  connectHandlers.add(handler);
  return () => {
    connectHandlers.delete(handler);
  };
}

/** Current connect generation — changes whenever the socket (re)connects. */
export function getSocketVersion(): number {
  return version;
}

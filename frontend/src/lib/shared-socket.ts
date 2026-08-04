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
import { queryClient } from "./queryClient";
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
 * `controller_id:timestamp`.
 */
function mergeSnapshots(
  existing: ControllerPerformanceSnapshot[],
  incoming: ControllerPerformanceSnapshot[],
): ControllerPerformanceSnapshot[] {
  const merged = [...existing];
  const seen = new Set(existing.map((s) => `${s.controller_id}:${s.timestamp}`));
  for (const snap of incoming) {
    const key = `${snap.controller_id}:${snap.timestamp}`;
    if (!seen.has(key)) {
      merged.push(snap);
      seen.add(key);
    }
  }
  return merged;
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
 */
function handleMessage(channel: string, data: unknown): void {
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

      // Key by controller_id (stable) — controller_name may differ between REST and WS
      const oldMap = new Map<string, ControllerInfo>();
      for (const c of old.controllers) {
        const key = `${c.bot_name}-${c.controller_id || c.controller_name}`;
        oldMap.set(key, c);
      }
      const oldBotMap = new Map(old.bots.map((b) => [b.bot_name, b]));

      return {
        ...incoming,
        controllers: incoming.controllers.map((c) => {
          const key = `${c.bot_name}-${c.controller_id || c.controller_name}`;
          const prev = oldMap.get(key);
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
    queryClient.setQueryData(["executors", server, ""], data);
    // Also update any filtered executor queries (e.g. ["executors", server, "main", pair])
    const allExecs = data as { controller_id?: string; trading_pair?: string }[];
    if (Array.isArray(allExecs)) {
      const cache = queryClient.getQueryCache().findAll({ queryKey: ["executors", server] });
      for (const entry of cache) {
        const key = entry.queryKey as string[];
        // Skip the unfiltered key (already set above)
        if (key.length <= 3 && key[2] === "") continue;
        // key format: ["executors", server, controllerId, pair]
        if (key.length === 4) {
          const [, , cid, tp] = key;
          const filtered = allExecs.filter(
            (ex) => (!cid || ex.controller_id === cid) && (!tp || ex.trading_pair === tp),
          );
          queryClient.setQueryData(key, filtered);
        }
      }
    }
    const execs = data as unknown[];
    if (Array.isArray(execs)) {
      queryClient.setQueryData(
        ["executors-infinite", server],
        (old: { pages?: { executors: unknown[]; next_cursor: string | null }[]; pageParams?: unknown[] } | undefined) => {
          if (!old?.pages?.length) return old;
          const firstPage = old.pages[0];
          const limit = firstPage.executors.length || 50;
          const nextFirst = {
            ...firstPage,
            executors: execs.slice(0, limit),
          };
          return { ...old, pages: [nextFirst, ...old.pages.slice(1)] };
        },
      );
    }
  } else if (prefix === "controller_perf") {
    // Update the "all controllers" sparkline cache
    const incoming = data as { snapshots?: ControllerPerformanceSnapshot[] };
    if (incoming?.snapshots) {
      queryClient.setQueryData(
        ["controller-perf-history-all", server],
        (old: ControllerPerformanceHistoryResponse | undefined) => {
          if (!old) return old;
          // Append new snapshots and deduplicate by controller_id+timestamp
          const merged = mergeSnapshots(old.snapshots ?? [], incoming.snapshots!);
          return { ...old, snapshots: merged };
        },
      );

      // Also update per-controller history caches
      const byController = new Map<string, ControllerPerformanceSnapshot[]>();
      for (const snap of incoming.snapshots) {
        const cid = snap.controller_id || snap.controller_name;
        if (!cid) continue;
        const arr = byController.get(cid) ?? [];
        arr.push(snap);
        byController.set(cid, arr);
      }
      for (const [cid, snaps] of byController) {
        queryClient.setQueryData(
          ["controller-perf-history", server, cid],
          (old: ControllerPerformanceHistoryResponse | undefined) => {
            if (!old) return old;
            const merged = mergeSnapshots(old.snapshots ?? [], snaps);
            return { ...old, snapshots: merged };
          },
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

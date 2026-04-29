import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import type { BotsPageResponse, ControllerInfo } from "@/lib/api";
import { CondorWebSocket } from "@/lib/websocket";

export function useCondorWebSocket(
  channels: string[],
  server: string | null,
) {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const wsRef = useRef<CondorWebSocket | null>(null);
  const [wsVersion, setWsVersion] = useState(0);

  useEffect(() => {
    if (!token || !server) return;

    const ws = new CondorWebSocket(token);
    wsRef.current = ws;

    // Track reconnects by polling version
    let versionPoll: ReturnType<typeof setInterval> | null = null;
    let lastVersion = ws.version;

    ws.onMessage((channel, data) => {
      // Update React Query cache based on channel prefix
      const prefix = channel.split(":")[0];

      // Candle data is now managed by candle-store.ts — skip here
      if (prefix === "candles") {
        // Still update candle status for error/connected indicators
        const parts = channel.split(":");
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

      if (prefix === "portfolio") {
        queryClient.setQueryData(["portfolio", server], data);
      } else if (prefix === "bots") {
        // Merge WS update with existing cache to preserve config/deployed_at from REST
        queryClient.setQueryData(["bots", server], (old: BotsPageResponse | undefined) => {
          const incoming = data as BotsPageResponse;
          if (!incoming?.controllers) return old ?? data;
          if (!old?.controllers?.length) return incoming;

          // Build lookup of existing controllers by key for config/deployed_at
          const oldMap = new Map<string, ControllerInfo>();
          for (const c of old.controllers) {
            oldMap.set(`${c.bot_name}-${c.controller_name}`, c);
          }
          const oldBotMap = new Map(old.bots.map((b) => [b.bot_name, b]));

          return {
            ...incoming,
            controllers: incoming.controllers.map((c) => {
              const prev = oldMap.get(`${c.bot_name}-${c.controller_name}`);
              if (!prev) return c;
              return {
                ...c,
                // Preserve rich data from REST fetch
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
        // Set unfiltered cache (matches default queryKey with status="")
        queryClient.setQueryData(["executors", server, ""], data);
        const execs = data as unknown[];
        if (Array.isArray(execs)) {
          queryClient.setQueryData(
            ["executors-infinite", server, ""],
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
      } else if (prefix === "orderbook") {
        const parts = channel.split(":");
        if (parts.length >= 4) {
          const [, srv, connector, pair] = parts;
          queryClient.setQueryData(["order-book", srv, connector, pair], data);
        }
      }
    });

    // Notify React immediately when WS connects (not via polling)
    ws.onConnect(() => setWsVersion((v) => v + 1));

    ws.connect();

    // Subscribe to requested channels
    for (const ch of channels) {
      ws.subscribe(ch);
    }

    // Poll as fallback for reconnects (in case onConnect misses edge cases)
    versionPoll = setInterval(() => {
      if (ws.version !== lastVersion) {
        lastVersion = ws.version;
        setWsVersion(ws.version);
      }
    }, 500);

    return () => {
      if (versionPoll) clearInterval(versionPoll);
      ws.disconnect();
      wsRef.current = null;
    };
  }, [token, server, channels.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  return { wsRef, wsVersion };
}

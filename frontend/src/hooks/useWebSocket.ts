import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import type { BotsPageResponse, ControllerInfo } from "@/lib/api";
import { candleStore } from "@/lib/candle-store";
import { CondorWebSocket } from "@/lib/websocket";

/**
 * Filter out candle channels — those are managed exclusively by candleStore.
 */
function nonCandleChannels(channels: string[]): string[] {
  return channels.filter((ch) => !ch.startsWith("candles:"));
}

export function useCondorWebSocket(
  channels: string[],
  server: string | null,
) {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const wsRef = useRef<CondorWebSocket | null>(null);
  const [wsVersion, setWsVersion] = useState(0);
  const prevChannelsRef = useRef<Set<string>>(new Set());

  // ── Effect 1: Create / destroy WS (stable — only depends on token + server) ──
  useEffect(() => {
    if (!token || !server) return;

    const ws = new CondorWebSocket(token);
    wsRef.current = ws;

    // Wire candle store singleton to this WS instance
    candleStore.setWs(ws);

    let lastVersion = ws.version;

    ws.onMessage((channel, data) => {
      const prefix = channel.split(":")[0];

      // Candle data is managed by candle-store.ts — only update status here
      if (prefix === "candles") {
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
        queryClient.setQueryData(["bots", server], (old: BotsPageResponse | undefined) => {
          const incoming = data as BotsPageResponse;
          if (!incoming?.controllers) return old ?? data;
          if (!old?.controllers?.length) return incoming;

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

    // Notify React immediately when WS connects
    ws.onConnect(() => setWsVersion((v) => v + 1));

    ws.connect();

    // Poll as fallback for reconnects
    const versionPoll = setInterval(() => {
      if (ws.version !== lastVersion) {
        lastVersion = ws.version;
        setWsVersion(ws.version);
      }
    }, 500);

    return () => {
      clearInterval(versionPoll);
      candleStore.setWs(null);
      ws.disconnect();
      wsRef.current = null;
      prevChannelsRef.current = new Set();
    };
  }, [token, server]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Effect 2: Diff channels — subscribe/unsubscribe without reconnecting ──
  useEffect(() => {
    const ws = wsRef.current;
    if (!ws) return;

    const newSet = new Set(nonCandleChannels(channels));
    const oldSet = prevChannelsRef.current;

    // Subscribe new channels
    for (const ch of newSet) {
      if (!oldSet.has(ch)) ws.subscribe(ch);
    }
    // Unsubscribe removed channels
    for (const ch of oldSet) {
      if (!newSet.has(ch)) ws.unsubscribe(ch);
    }

    prevChannelsRef.current = newSet;
  }, [channels.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  return { wsRef, wsVersion };
}

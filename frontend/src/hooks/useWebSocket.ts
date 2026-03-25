import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
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
      if (prefix === "portfolio") {
        queryClient.setQueryData(["portfolio", server], data);
      } else if (prefix === "bots") {
        queryClient.setQueryData(["bots", server], data);
      } else if (prefix === "executors") {
        queryClient.setQueryData(["executors", server], data);
      } else if (prefix === "candles") {
        // channel format: candles:{server}:{connector}:{pair}:{interval}
        const parts = channel.split(":");
        if (parts.length >= 5) {
          const [, srv, connector, pair, interval] = parts;
          const payload = data as {
            type: string;
            candle?: Record<string, number>;
            data?: Record<string, number>[];
            message?: string;
          };

          if (payload.type === "error") {
            queryClient.setQueryData(
              ["candles-status", srv, connector, pair, interval],
              { status: "error", message: payload.message ?? "Unknown error" },
            );
          } else if (payload.type === "candle_update" && payload.candle) {
            queryClient.setQueryData(
              ["candles-status", srv, connector, pair, interval],
              { status: "connected" },
            );
            // Update the last candle in place
            queryClient.setQueryData(
              ["candles", srv, connector, pair, interval],
              (old: Record<string, number>[] | undefined) => {
                if (!old?.length) return old;
                const updated = [...old];
                const last = updated[updated.length - 1];
                if (last && last.timestamp === payload.candle!.timestamp) {
                  updated[updated.length - 1] = payload.candle!;
                } else {
                  updated.push(payload.candle!);
                }
                return updated;
              },
            );
          } else if (payload.type === "candles" && payload.data) {
            queryClient.setQueryData(
              ["candles-status", srv, connector, pair, interval],
              { status: "connected" },
            );
            // New candle(s) arrived — append
            queryClient.setQueryData(
              ["candles", srv, connector, pair, interval],
              (old: Record<string, number>[] | undefined) => {
                if (!old) return payload.data;
                const lastTs = old[old.length - 1]?.timestamp ?? 0;
                const newCandles = payload.data!.filter(
                  (c) => c.timestamp > lastTs,
                );
                return newCandles.length ? [...old, ...newCandles] : old;
              },
            );
          }
        }
      }
    });

    ws.connect();

    // Subscribe to requested channels
    for (const ch of channels) {
      ws.subscribe(ch);
    }

    // Poll for version changes to detect reconnects
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

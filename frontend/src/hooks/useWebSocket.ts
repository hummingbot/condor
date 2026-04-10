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
        // Set unfiltered cache (matches default queryKey with status="")
        queryClient.setQueryData(["executors", server, ""], data);
        // NOTE: do NOT invalidate ["executors-infinite", server] here.
        // The Executors page loads pages progressively; invalidating on every
        // WS tick (every ~2s) restarts pagination and the page never finishes
        // loading. The infinite query has its own refetchInterval, and we
        // prime its first page from WS data below so updates still flow.
        const execs = data as unknown[];
        if (Array.isArray(execs)) {
          queryClient.setQueryData(
            ["executors-infinite", server, ""],
            (old: { pages?: { executors: unknown[]; next_cursor: string | null }[]; pageParams?: unknown[] } | undefined) => {
              if (!old?.pages?.length) return old;
              const firstPage = old.pages[0];
              // Replace first page contents with the live WS snapshot (capped
              // to the same page size) so the top of the list stays fresh.
              const limit = firstPage.executors.length || 50;
              const nextFirst = {
                ...firstPage,
                executors: execs.slice(0, limit),
              };
              return { ...old, pages: [nextFirst, ...old.pages.slice(1)] };
            },
          );
        }
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
            // Update or append a single candle
            queryClient.setQueryData(
              ["candles", srv, connector, pair, interval],
              (old: Record<string, number>[] | undefined) => {
                if (!old?.length) return old;
                const ts = payload.candle!.timestamp;
                const lastIdx = old.length - 1;
                if (old[lastIdx].timestamp === ts) {
                  // Update last candle in place
                  const updated = [...old];
                  updated[lastIdx] = payload.candle!;
                  return updated;
                } else if (ts > old[lastIdx].timestamp) {
                  // New candle after the last one
                  return [...old, payload.candle!];
                }
                // Candle for an older timestamp — ignore
                return old;
              },
            );
          } else if (payload.type === "candles" && payload.data?.length) {
            queryClient.setQueryData(
              ["candles-status", srv, connector, pair, interval],
              { status: "connected" },
            );
            // Merge candles by timestamp — keeps both old REST data and new WS data
            queryClient.setQueryData(
              ["candles", srv, connector, pair, interval],
              (old: Record<string, number>[] | undefined) => {
                if (!old?.length) return payload.data;
                // Build a map from existing candles (keyed by timestamp)
                const map = new Map<number, Record<string, number>>();
                for (const c of old) map.set(c.timestamp, c);
                // Merge incoming: newer data wins for same timestamp
                let changed = false;
                for (const c of payload.data!) {
                  if (!map.has(c.timestamp)) {
                    map.set(c.timestamp, c);
                    changed = true;
                  } else {
                    // Update existing candle (WS may have more recent OHLCV)
                    const existing = map.get(c.timestamp)!;
                    if (
                      existing.close !== c.close ||
                      existing.high !== c.high ||
                      existing.low !== c.low ||
                      existing.volume !== c.volume
                    ) {
                      map.set(c.timestamp, c);
                      changed = true;
                    }
                  }
                }
                if (!changed) return old;
                // Sort by timestamp and return
                return Array.from(map.values()).sort(
                  (a, b) => a.timestamp - b.timestamp,
                );
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

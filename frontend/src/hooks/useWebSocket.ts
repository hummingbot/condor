import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import {
  acquireSocket,
  getSocketVersion,
  onSocketConnect,
  releaseSocket,
  subscribeChannel,
  unsubscribeChannel,
} from "@/lib/shared-socket";
import type { CondorWebSocket } from "@/lib/websocket";

/**
 * Filter out candle channels — those are managed exclusively by candleStore.
 */
function nonCandleChannels(channels: string[]): string[] {
  return channels.filter((ch) => !ch.startsWith("candles:"));
}

/**
 * Subscribe to Condor WS channels for as long as the calling component is mounted.
 *
 * Every caller shares one connection (see `lib/shared-socket.ts`); this hook
 * only holds references — one on the socket, one per channel — and hands back
 * the shared instance for callers that want to read raw frames themselves.
 */
export function useCondorWebSocket(
  channels: string[],
  server: string | null,
) {
  const { token } = useAuth();
  const wsRef = useRef<CondorWebSocket | null>(null);
  const [wsVersion, setWsVersion] = useState(getSocketVersion);
  const prevChannelsRef = useRef<Set<string>>(new Set());

  // ── Effect 1: Hold a reference on the shared socket ──
  useEffect(() => {
    if (!token) return;

    wsRef.current = acquireSocket(token);

    // Mounting into an already-open socket needs no catch-up: `wsVersion` was
    // seeded from the live version at render, and `connect()` can only reach
    // `onopen` in a later task, so no bump can slip past this registration.
    const offConnect = onSocketConnect(() => setWsVersion(getSocketVersion()));

    return () => {
      offConnect();
      wsRef.current = null;
      releaseSocket();
    };
  }, [token]);

  // ── Effect 2: Diff channels — subscribe/unsubscribe without reconnecting ──
  useEffect(() => {
    // Bare channel names (e.g. "bots") need server prefix ("bots:local")
    // to match the backend broadcast channel format. Without a server nothing
    // can be resolved, so the previous set is simply released.
    const resolved = server
      ? nonCandleChannels(channels).map((ch) =>
          ch.includes(":") ? ch : `${ch}:${server}`,
        )
      : [];
    const newSet = new Set(resolved);
    const oldSet = prevChannelsRef.current;

    // Diff rather than release-and-reacquire: a channel both sets share must
    // not flap on the wire just because a sibling channel was added.
    for (const ch of newSet) {
      if (!oldSet.has(ch)) subscribeChannel(ch);
    }
    for (const ch of oldSet) {
      if (!newSet.has(ch)) unsubscribeChannel(ch);
    }

    prevChannelsRef.current = newSet;
  }, [channels.join(","), server]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Effect 3: Release every held channel on unmount ──
  // Separate from the diffing effect on purpose: that one re-runs whenever the
  // channel list changes, and a cleanup there would drop and re-take references
  // the component never actually stopped needing.
  useEffect(() => {
    return () => {
      for (const ch of prevChannelsRef.current) unsubscribeChannel(ch);
      prevChannelsRef.current = new Set();
    };
  }, []);

  return { wsRef, wsVersion };
}

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type DexUpstream } from "@/lib/api";

/** Idle poll. Reads process-local counters, so it costs nothing upstream. */
const POLL_HEALTHY_MS = 20_000;
/** While throttled: often enough that the banner clears on its own. */
const POLL_LIMITED_MS = 5_000;
/**
 * How long one refusal keeps the banner up.
 *
 * A spent per-minute budget refuses a request without opening the breaker, so
 * there is no cooldown to count down against — the only evidence is a counter
 * that ticked. Long enough to be read, short enough to clear itself once the
 * window drains.
 */
const STICKY_MS = 30_000;

export interface DexUpstreamState {
  /** Show the notice: gecko is refusing, or refused something moments ago. */
  limited: boolean;
  /** Whole seconds left on the cooldown; 0 when there is no cooldown to wait on. */
  retryIn: number;
  requestsLastMinute: number;
  budget: number;
  /**
   * Report the `upstream` block that came back with a pool response.
   *
   * The poll finds out within a few seconds anyway, but the request the user is
   * looking at knows *now* — and the table they are staring at is the one that
   * came back empty. `updatedAt` is when that response was received: a cached
   * page re-rendered later carries a throttle flag that has long since expired.
   */
  report: (upstream: DexUpstream | undefined, updatedAt: number) => void;
}

/**
 * Whether GeckoTerminal is throttling Condor, for the DEX views that depend on it.
 *
 * Every pool row, pool page and chart on the dashboard shares one per-IP budget,
 * and everything behind that budget degrades to an empty list when it is spent.
 * Without this the DEX panel shows "No pools found" for a chain with thousands of
 * them, and a chart that simply stops moving — three failure modes that look like
 * the product being broken rather than a limit that clears in seconds.
 *
 * Refills the DEX queries when the limit clears, so the tables come back on their
 * own rather than waiting for the user to suspect a reload might help.
 */
export function useDexUpstream(server: string | null): DexUpstreamState {
  const queryClient = useQueryClient();
  const [stickyUntil, setStickyUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const lastCounter = useRef<number | null>(null);
  const wasLimited = useRef(false);

  const { data, dataUpdatedAt } = useQuery({
    queryKey: ["dex-upstream", server],
    // The diff lives in the fetch rather than in an effect on its result: a
    // refusal between two polls leaves no flag behind — only these monotonic
    // counters move — and comparing them is something the fetch already knows
    // how to do without a render pass in between.
    queryFn: async () => {
      const state = await api.getDexUpstream(server!);
      const counter = state.throttled_calls + state.count_429;
      const previous = lastCounter.current;
      lastCounter.current = counter;
      if ((previous !== null && counter > previous) || state.rate_limited) {
        setNow(Date.now());
        setStickyUntil(Date.now() + STICKY_MS);
      }
      return state;
    },
    enabled: !!server,
    // Stops polling if the endpoint is not there (an older backend): a banner
    // nobody can raise is not worth a request every twenty seconds.
    retry: false,
    refetchInterval: (query) =>
      query.state.status === "error"
        ? false
        : query.state.data?.rate_limited
          ? POLL_LIMITED_MS
          : POLL_HEALTHY_MS,
    // A throttled dashboard is exactly when a background tab should stop asking.
    refetchIntervalInBackground: false,
  });

  const report = useCallback((upstream: DexUpstream | undefined, updatedAt: number) => {
    if (!upstream || Date.now() - updatedAt > STICKY_MS) return;
    if (upstream.throttled_request || upstream.rate_limited) {
      setNow(Date.now());
      setStickyUntil(Date.now() + STICKY_MS);
    }
  }, []);

  const cooldownEnd =
    data?.rate_limited && dataUpdatedAt
      ? dataUpdatedAt + data.retry_in * 1000
      : 0;
  const limited = now < cooldownEnd || now < stickyUntil;

  // Only while something is counting down: an idle DEX page re-renders nothing.
  useEffect(() => {
    if (!limited) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [limited]);

  useEffect(() => {
    if (wasLimited.current && !limited) {
      // The rows that came back empty are cached as if they were the answer.
      for (const key of ["dex-pools", "dex-pool-by-address", "dex-favorites"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    }
    wasLimited.current = limited;
  }, [limited, queryClient]);

  return {
    limited,
    retryIn: Math.max(0, Math.ceil((cooldownEnd - now) / 1000)),
    requestsLastMinute: data?.requests_last_minute ?? 0,
    budget: data?.budget ?? 0,
    report,
  };
}

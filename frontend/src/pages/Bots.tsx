import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Bot, Rocket } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { PerfBrowser } from "@/components/perf/PerfBrowser";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import { useRates } from "@/hooks/useRates";
import { useServer } from "@/hooks/useServer";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import {
  api,
  type ControllerInfo,
  type ControllerPerformanceHistoryAllResponse,
  type ExecutorInfo,
} from "@/lib/api";
import { parsePopulation } from "@/lib/perf-tree";
import { controllerKey } from "@/lib/controller-identity";
import { historyRowBudget } from "@/lib/history-pagination";
import {
  HISTORY_REFETCH_MS,
  TAIL_MAX_PAGES,
  refreshControllerHistory,
} from "@/lib/history-refresh";
import { samplingIntervalSince } from "@/lib/pnl-chart";

const BOTS_WS_CHANNELS = ["bots", "controller_perf"];

/**
 * The executor walk: 500 a page, four pages by default.
 *
 * The same bounded walk `/executors` made, under the same query key, so the
 * shared socket's live merge and the chat's route facts both still find it
 * (PERF/FEAT-072). The cap now bounds the *sidebar tree* as well as a table, so
 * the browser is told where the walk stopped and says so.
 */
const EXECUTOR_PAGE_SIZE = 500;
const EXECUTOR_PAGES = 4;

/**
 * `/bots` is the controller browser (FEAT-084).
 *
 * The tab bar, the stat-card strip, the sortable controllers table and the bots
 * accordion that used to stand in front of it are gone: the browser's scope
 * sidebar (fleet → bot → controller) *is* the page, and every bot-level action
 * that lived in the accordion is reachable from the scope it belongs to. What
 * is left here is the data the browser reports on — the fleet query and the
 * performance-history walk, which used to live in `ActiveBotsTab`.
 *
 * `?tab=runs` is the one interim exception: the run history is still its own
 * padded table until [[FEAT-086]] folds it into the browser's Terminated
 * population. `?tab=archived` is the retired link Runs absorbed.
 */
export function Bots() {
  const [searchParams] = useSearchParams();
  const population = parsePopulation(searchParams.get("population"));

  const { server } = useServer();
  // `?tab=runs` was the run history's own padded table, and `?tab=archived` the
  // retired link it absorbed. Both are the Terminated population now, so the
  // old links land on the scope that answers them (FEAT-086).
  const tab = searchParams.get("tab");
  const legacyRunsTab = tab === "runs" || tab === "archived";
  // Deploy lives in the browser's fleet-scope header — except when there is no
  // fleet to scope, which is exactly when it is needed most (see below).
  const [showDeploy, setShowDeploy] = useState(false);

  // Real-time updates for everything the browser folds: the fleet, its
  // performance snapshots, and the executors underneath it — the last of which
  // came with the executors page and has to come with it (FEAT-086).
  const wsChannels = useMemo(
    () => (server ? [...BOTS_WS_CHANNELS, `executors:${server}`] : BOTS_WS_CHANNELS),
    [server],
  );
  useCondorWebSocket(wsChannels, server);

  const { data, isLoading, error } = useQuery({
    queryKey: ["bots", server],
    queryFn: () => api.getBots(server!),
    enabled: !!server,
    refetchInterval: 30000, // Slower polling since WS handles real-time updates
  });

  // Compute earliest deploy time from active bots for filtering perf history
  const botList = data?.bots;
  const earliestDeploy = useMemo(() => {
    if (!botList?.length) return undefined;
    let earliest: number | undefined;
    for (const bot of botList) {
      if (bot.deployed_at) {
        const ms = Date.parse(bot.deployed_at);
        if (!isNaN(ms) && (earliest === undefined || ms < earliest)) earliest = ms;
      }
    }
    return earliest ? new Date(earliest).toISOString() : undefined;
  }, [botList]);

  // How finely to sample the fleet's history depends on how long the fleet has
  // actually been running: pinned at 5m, a month-old fleet asked for 8,640
  // points per controller to draw what 720 hourly ones draw identically — and
  // since the route caps a page at 1000 rows it got the first 1000 and drew a
  // truncated history (PERF-238). Derived from the same `earliestDeploy` the
  // request sends as `start_time`, so the interval always describes the window
  // actually asked for.
  const perfInterval = useMemo(() => samplingIntervalSince(earliestDeploy), [earliestDeploy]);

  // Fetch the fleet's performance history (all controllers at once).
  //
  // Walked page by page rather than requested once: a page is capped at 1000
  // ROWS and the sampler writes one row per controller per dump, so the single
  // request this used to make showed 1000/N instants — eight hours for ten
  // controllers, four for twenty, whatever `start_time` asked for (CORR-237).
  // The row budget is sized to the fleet for the same reason: the interval was
  // already chosen so the span fits ~1000 *instants* (PERF-238), and N
  // controllers turn each of those into up to N rows.
  const { data: perfHistory } = useQuery({
    // The interval is part of the key so two resolutions never share a cache
    // entry, and last in it so the shared socket's prefix-matched live merge
    // (`mergeIntoMatchingQueries`) still finds this query (PERF-238).
    queryKey: ["controller-perf-history-all", server, earliestDeploy, perfInterval],
    // Full on the first load, a tail on every one after it (PERF-239). The
    // `controller_perf` channel this page subscribes to writes each fleet
    // snapshot straight into this cache entry every 30s, so the old 120s poll
    // re-downloaded a history that had already arrived — and after CORR-237 it
    // re-downloaded it as up to ten sequential requests. `previous` is read
    // back under this query's own key, which is what ties the refresh to one
    // resolution: the key ends with `perfInterval` (PERF-238), so a coarser and
    // a finer series each extend themselves and never each other.
    queryFn: ({ signal, queryKey: key, client }) => {
      const budget = historyRowBudget(data?.controllers?.length ?? 0);
      const load = (startTime: string | undefined, maxPages?: number) =>
        api.getControllerPerformanceHistoryAll(
          server!,
          { interval: perfInterval, start_time: startTime },
          { maxRows: budget, maxPages, signal },
        );
      return refreshControllerHistory({
        previous: client.getQueryData<ControllerPerformanceHistoryAllResponse>(key),
        interval: perfInterval,
        full: () => load(earliestDeploy),
        tail: (from) => load(from, TAIL_MAX_PAGES),
        maxRows: budget,
      });
    },
    enabled: !!server && (data?.controllers?.length ?? 0) > 0,
    // The socket is the update path; this is only the net under it.
    refetchInterval: HISTORY_REFETCH_MS,
    staleTime: 60_000,
  });

  // Deduplicate controllers by bot_name + controller_id (WS updates can cause duplicates)
  const controllers = useMemo(() => {
    const raw = data?.controllers ?? [];
    const seen = new Map<string, ControllerInfo>();
    for (const ctrl of raw) {
      seen.set(controllerKey(ctrl), ctrl); // last wins (most recent data)
    }
    return Array.from(seen.values());
  }, [data?.controllers]);

  // The order the sidebar draws them in. The table this replaced could sort by
  // any column and defaulted to this one; the sidebar keeps the default, which
  // is the ranking the sort was mostly used for.
  const sortedControllers = useMemo(
    () => [...controllers].sort((a, b) => b.global_pnl_quote - a.global_pnl_quote),
    [controllers],
  );

  // Filter performance snapshots to only active controllers and current run
  const activeSnapshots = useMemo(() => {
    if (!perfHistory?.snapshots || controllers.length === 0) return [];

    // Build set of active controller keys and their deploy times. Keyed by
    // bot + controller, because a bare controller id is a config id two bots
    // can share: one map entry per id meant last-write-wins on the deploy
    // time, so an hour-old bot truncated its five-day sibling's history to an
    // hour of points (CORR-241).
    const activeControllers = new Map<string, number>(); // key -> deployedAt ms
    for (const ctrl of controllers) {
      const deployMs = ctrl.deployed_at ? Date.parse(ctrl.deployed_at) : 0;
      activeControllers.set(controllerKey(ctrl), deployMs);
    }

    return perfHistory.snapshots.filter((snap) => {
      const key = controllerKey(snap);
      if (!key || !activeControllers.has(key)) return false;
      const deployMs = activeControllers.get(key)!;
      if (!deployMs) return true; // no deploy time known, keep it
      const snapMs = Date.parse(snap.timestamp) || 0;
      return snapMs >= deployMs;
    });
  }, [perfHistory, controllers]);

  // ── The executors the browser hangs under those controllers ──

  const [maxPages, setMaxPages] = useState(EXECUTOR_PAGES);
  const {
    data: executorPages,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["executors-infinite", server],
    enabled: !!server,
    initialPageParam: "" as string,
    queryFn: ({ pageParam }) =>
      api.getExecutorsPage(server!, { cursor: pageParam || undefined, limit: EXECUTOR_PAGE_SIZE }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: 60_000, // Slow fallback only — WS handles real-time updates
    refetchOnWindowFocus: false,
  });

  // Progressive loading: ask for the next chunk as soon as the current arrives,
  // up to the cap the reader has allowed.
  const loadedPages = executorPages?.pages.length ?? 0;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && loadedPages < maxPages) {
      fetchNextPage();
    }
  }, [hasNextPage, isFetchingNextPage, loadedPages, maxPages, fetchNextPage]);

  const executors = useMemo(
    () => (executorPages?.pages.flatMap((p) => p?.executors ?? []) ?? []) as ExecutorInfo[],
    [executorPages],
  );

  // The finished runs, which only the Terminated population reports — so the
  // request is not made at all while the reader is looking at the live fleet.
  const { data: runsData } = useQuery({
    queryKey: ["bot-runs", server],
    queryFn: () => api.getBotRuns(server!, { limit: 200 }),
    enabled: !!server && population === "terminated",
    refetchInterval: 30_000,
  });

  const loadMore = useCallback(() => setMaxPages((p) => p + EXECUTOR_PAGES), []);
  const paging = useMemo(
    () => ({
      loaded: executors.length,
      loading: isFetchingNextPage,
      done: !hasNextPage && executors.length > 0,
      capped: loadedPages >= maxPages && !!hasNextPage,
      loadMore,
    }),
    [executors.length, isFetchingNextPage, hasNextPage, loadedPages, maxPages, loadMore],
  );

  // Currency conversion, over every quote on screen — the controllers' and the
  // executors' alike, since both are folded into the same totals now.
  const quoteCurrencies = useMemo(
    () => [
      ...controllers.map((c) => c.trading_pair?.split("-")[1] || "USDT"),
      ...executors.map((ex) => ex.trading_pair?.split("-")[1] || "USDT"),
    ],
    [controllers, executors],
  );
  const {
    convert,
    formatPnlValue,
    formatValue,
    formatValueDetailed,
    resolvedSymbol: currencySymbol,
  } = useRates(quoteCurrencies);

  if (legacyRunsTab) {
    return <Navigate to="/bots?population=terminated&group=bot" replace />;
  }

  if (!server) {
    return (
      <div className="p-6">
        <NoServerCard message="Select a server from the sidebar to view active bots." />
      </div>
    );
  }
  if (isLoading) return <FallbackSpinner />;
  if (error)
    return (
      <p className="p-6 text-[var(--color-red)]">
        {error instanceof Error ? error.message : "Error"}
      </p>
    );

  if (data?.server_online === false) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-3">
          <p className="text-sm font-medium text-[var(--color-yellow)]">
            Unable to reach server
          </p>
          {data.error_hint && (
            <p className="text-xs text-[var(--color-text-muted)] mt-1">{data.error_hint}</p>
          )}
        </div>
      </div>
    );
  }

  // Nothing to scope: the browser draws nothing without controllers, and the
  // fleet header that carries Deploy is part of the browser — so the empty
  // state has to carry the one action that gets out of it.
  if (controllers.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-[var(--color-text-muted)]">
        <Bot className="h-10 w-10" />
        <p>No bots running</p>
        <button
          onClick={() => setShowDeploy(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
        >
          <Rocket className="h-4 w-4" />
          Deploy Bot
        </button>
        <DeployBotDialog open={showDeploy} onClose={() => setShowDeploy(false)} server={server} />
      </div>
    );
  }

  return (
    <PerfBrowser
      controllers={sortedControllers}
      bots={data?.bots ?? []}
      server={server}
      convert={convert}
      currencySymbol={currencySymbol}
      // The fleet history this page walked: the browser's combined scopes fold
      // these rows rather than issuing a second walk of their own.
      snapshots={activeSnapshots}
      truncated={perfHistory?.truncated ?? false}
      executors={executors}
      paging={paging}
      runs={runsData?.runs ?? []}
      rateFormatPnl={formatPnlValue}
      rateFormatValue={formatValue}
      rateFormatDetailed={formatValueDetailed}
    />
  );
}

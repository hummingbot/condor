import { useQuery } from "@tanstack/react-query";
import { Bot, Rocket } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

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
} from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { historyRowBudget } from "@/lib/history-pagination";
import {
  HISTORY_REFETCH_MS,
  TAIL_MAX_PAGES,
  refreshControllerHistory,
} from "@/lib/history-refresh";
import { samplingIntervalSince } from "@/lib/pnl-chart";

const BotRunsTab = lazy(() =>
  import("@/pages/tabs/BotRunsTab").then((m) => ({ default: m.BotRunsTab })),
);

const BOTS_WS_CHANNELS = ["bots", "controller_perf"];

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
  const tab = searchParams.get("tab") ?? "";
  const onRuns = tab === "runs" || tab === "archived";

  const { server } = useServer();
  // Deploy lives in the browser's fleet-scope header — except when there is no
  // fleet to scope, which is exactly when it is needed most (see below).
  const [showDeploy, setShowDeploy] = useState(false);

  // Subscribe to real-time bots updates via WS
  useCondorWebSocket(BOTS_WS_CHANNELS, server);

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

  // Currency conversion
  const quoteCurrencies = useMemo(
    () => controllers.map((c) => c.trading_pair?.split("-")[1] || "USDT"),
    [controllers],
  );
  const { convert, resolvedSymbol: currencySymbol } = useRates(quoteCurrencies);

  // The shell gives `/bots` no padding (`FULL_BLEED_ROUTES`), so everything
  // that is not the browser asks for its own.
  if (onRuns) {
    return (
      <div className="h-full overflow-auto p-6">
        <Suspense fallback={<FallbackSpinner />}>
          <BotRunsTab />
        </Suspense>
      </div>
    );
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
    />
  );
}

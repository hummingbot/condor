import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, type ControllerInfo, type ControllerPerformanceHistoryAllResponse } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { historyRowBudget } from "@/lib/history-pagination";
import {
  HISTORY_REFETCH_MS,
  TAIL_MAX_PAGES,
  refreshControllerHistory,
} from "@/lib/history-refresh";
import { aggregatePnlSeries, samplingIntervalSince } from "@/lib/pnl-chart";
import { controllerPerfHistoryQuery } from "@/lib/queryClient";
import type { ConvertFn } from "@/lib/rates";
import { PnlEvolutionChart } from "./PnlEvolutionChart";

// ── Component ──

interface Props {
  server: string;
  controllerId: string;
  botName: string;
  deployedAt?: string | null;
  height?: number;
  currencySymbol?: string;
  convert?: ConvertFn;
  controller?: ControllerInfo;
}

/**
 * One controller's PNL chart: this component owns the history query and the
 * loading/empty states around it. The drawing is PnlEvolutionChart's (ARCH-242)
 * and the fold is aggregatePnlSeries' (ARCH-243).
 */
export function ControllerPnlChart({ server, controllerId, botName, deployedAt, height = 400, currencySymbol = "$", convert, controller }: Props) {
  // How finely to sample depends on how long this controller has actually been
  // running: a month-old bot at 5m is 8,640 points to draw a line 720 hourly
  // ones draw identically — and the route caps a page at 1000 rows, so asking
  // for them silently returned a truncated history (PERF-238). Memoised on the
  // deploy time rather than recomputed from a live clock each render: the
  // thresholds are days apart, so a session that crosses one can carry the
  // finer interval until it remounts, and in exchange the query key is stable.
  const interval = useMemo(() => samplingIntervalSince(deployedAt), [deployedAt]);

  const { data: raw, isLoading } = useQuery({
    // Built by the factory, not by hand: the shared socket routes live frames
    // into this entry by a prefix of the key, and the ordering that keeps that
    // working — bot before controller, interval last — is stated once on
    // `controllerPerfHistoryQuery` (ARCH-285).
    queryKey: controllerPerfHistoryQuery(server, {
      botName,
      controllerId,
      start: deployedAt,
      interval,
    }).queryKey,
    // The first load is walked page by page: a page holds 1000 ROWS, and while
    // one controller is usually one row per instant, the bucketing is not
    // guaranteed to be — so the ceiling on a single request is a ceiling on the
    // visible window unless the cursor is followed (CORR-237). Every page
    // carries `interval` unchanged, so the series is one resolution end to end
    // (PERF-238).
    //
    // Every *later* load is a tail: the `controller_perf` channel has been
    // pushing this controller's newest snapshot into this very entry all along,
    // so re-requesting the window from `deployedAt` re-downloaded a history
    // that was already here — and after CORR-237, re-downloaded it as several
    // sequential requests (PERF-239). The previous entry is read from the cache
    // rather than from a ref because that is what the refresh has to extend,
    // and because the socket writes into it between refreshes. Reading it under
    // this query's own key is also what keeps the interval part of the cache
    // identity: the key ends with it (PERF-238), so a tail can never be spliced
    // onto a series sampled at another resolution.
    queryFn: ({ signal, queryKey: key, client }) => {
      const budget = historyRowBudget(1);
      const load = (startTime: string | undefined, maxPages?: number) =>
        api.getControllerPerformanceHistoryAll(
          server,
          {
            controller_id: controllerId,
            bot_name: botName,
            interval,
            start_time: startTime,
          },
          { maxRows: budget, maxPages, signal },
        );
      return refreshControllerHistory({
        previous: client.getQueryData<ControllerPerformanceHistoryAllResponse>(key),
        interval,
        full: () => load(deployedAt ?? undefined),
        tail: (from) => load(from, TAIL_MAX_PAGES),
        maxRows: budget,
      });
    },
    // The socket is the update path; this is only the net under it.
    refetchInterval: HISTORY_REFETCH_MS,
    staleTime: 30_000,
  });

  const snapshots = raw?.snapshots ?? [];

  // The fold keys on bot + controller, so the enabled set holds that composite
  // and not the bare id (CORR-241).
  const seriesKey = controllerKey({ bot_name: botName, controller_id: controllerId });

  // One controller is the degenerate case of the aggregate fold: a single
  // enabled key, so the shared function does the sorting, the conversion and
  // the live "now" point (ARCH-243) instead of a second hand-rolled copy here.
  const data = useMemo(
    () => aggregatePnlSeries(snapshots, new Set([seriesKey]), controller ? [controller] : [], convert),
    [snapshots, seriesKey, controller, convert],
  );

  if (isLoading) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center" style={{ height }}>
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Loading performance history...
        </div>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center" style={{ height }}>
        <p className="text-xs text-[var(--color-text-muted)]">No performance history available</p>
      </div>
    );
  }

  const pnlH = Math.round(height * 0.65);

  return (
    <PnlEvolutionChart
      data={data}
      title="PnL Evolution"
      pnlHeight={pnlH}
      volumeHeight={height - pnlH}
      currencySymbol={currencySymbol}
      notice={
        raw?.truncated
          ? {
              label: "partial history",
              detail:
                "This controller has more stored history than one chart may load at once, so the series starts later than its deploy.",
            }
          : undefined
      }
    />
  );
}

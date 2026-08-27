import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, type ControllerInfo } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { aggregatePnlSeries } from "@/lib/pnl-chart";
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
  const { data: raw, isLoading } = useQuery({
    // `bot_name` is part of the key, not just the request: the query asks
    // upstream for one bot's rows, so two bots running the same controller
    // config would otherwise share a cache entry — and the socket, which routes
    // live frames by this key's prefix, would push each bot's snapshots into
    // the other's chart (CORR-241).
    queryKey: ["controller-perf-history", server, botName, controllerId, deployedAt],
    queryFn: () =>
      api.getControllerPerformanceHistory(server, {
        controller_id: controllerId,
        bot_name: botName,
        interval: "5m",
        limit: 1000,
        start_time: deployedAt ?? undefined,
      }),
    refetchInterval: 60_000,
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
    />
  );
}

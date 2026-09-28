import { metricsToDataPoints } from "@/components/agent/AgentPnlChart";
import type { MetricEntry } from "@/lib/parse-agent";

/**
 * The points a run's PnL chart draws, oldest first.
 *
 * Prefer the series derived from the bots' own history: the journal snapshots
 * are only what the aggregator believed at each tick, so a session that ran
 * while it could not see its bots has a permanently flat record of zeros.
 * Fall back to the snapshots for pure executor sessions, which own no bot and
 * so have no history to derive from.
 *
 * Pure, and out of the chart, so the Now card can know whether there is a
 * chart to draw before it lays itself out (ARCH-426).
 */
export function sessionPnlPoints(
  metrics: MetricEntry[],
  pnlSeries?: { timestamp: string; pnl: number }[] | null,
): { time: number; value: number }[] {
  if (pnlSeries?.length) {
    return pnlSeries
      .filter((p) => p.timestamp)
      .map((p) => ({
        time: Math.floor(new Date(p.timestamp).getTime() / 1000),
        value: p.pnl,
      }))
      .sort((a, b) => a.time - b.time);
  }
  return metricsToDataPoints(metrics);
}

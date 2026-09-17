import { useMemo } from "react";

import { AgentPnlChart, metricsToDataPoints } from "@/components/agent/AgentPnlChart";
import type { AgentPerformance } from "@/lib/api";
import type { ParsedJournal } from "@/lib/parse-agent";

// ── Session Overview ──

export function SessionOverview(props: {
  journal: ParsedJournal;
  perf?: AgentPerformance | null;
  pnlSeries?: { timestamp: string; pnl: number }[] | null;
}) {
  const { metrics } = props.journal;
  const { pnlSeries } = props;

  // Prefer the series derived from the bots' own history: the journal snapshots
  // are only what the aggregator believed at each tick, so a session that ran
  // while it could not see its bots has a permanently flat record of zeros.
  // Fall back to the snapshots for pure executor sessions, which own no bot and
  // so have no history to derive from.
  const pnlData = useMemo(() => {
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
  }, [pnlSeries, metrics]);

  if (pnlData.length <= 1) {
    return null;
  }

  return (
    <div className="space-y-4">
      <AgentPnlChart
        data={pnlData}
        height={400}
        title={pnlSeries?.length ? "Realized PnL" : "Metrics Timeline"}
      />
    </div>
  );
}

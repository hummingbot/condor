import { AgentPnlChart } from "@/components/agent/AgentPnlChart";

// ── Session Overview ──
//
// The run's PnL curve, drawn bare: it lives inside the Now card, whose header
// already names it (ARCH-426). The points come from `sessionPnlPoints`, and
// the card only asks for this once there are at least two of them.

export function SessionOverview({
  data,
  height,
}: {
  data: { time: number; value: number }[];
  height: number;
}) {
  return <AgentPnlChart data={data} height={height} bare />;
}

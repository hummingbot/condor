import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { tickCountdownLabel } from "@/components/agent/workspace/fleet";
import { api } from "@/lib/api";
import type { AgentSummary } from "@/lib/api";
import type { FleetOwner, LiveLoop } from "@/lib/agent-attribution";
import { formatRelativeTime } from "@/lib/formatters";

/** A `FleetOwner` whose loop is actually going — `live` narrowed off `null`. */
export type LiveFleetOwner = FleetOwner & { live: LiveLoop };

/**
 * The loop's whole detail line: session, tick, cadence and last tick — the one
 * wording every surface that reports a loop's facts has to share (FEAT-124),
 * because a reader who sees the same loop in two places must not be told two
 * different things about it. `LoopsPanel` and `DockExecution`'s `AgentRow`
 * both call this rather than each wording it themselves.
 */
export function loopSummaryLabel(live: LiveLoop, nowMs: number): string {
  const facts = [`session ${live.sessionNum}`, `tick ${live.tickCount}`];
  if (live.status === "running") {
    facts.push(
      live.lastTickAt <= 0
        ? "first tick pending"
        : tickCountdownLabel(live.lastTickAt + live.frequencySec - nowMs / 1000),
    );
  }
  const summary = facts.join(" · ");
  return live.lastTickAt > 0
    ? `${summary} · last tick ${formatRelativeTime(live.lastTickAt)}`
    : summary;
}

/**
 * Running before paused, then agent name, then strategy name — a stable order
 * for a list that reshuffles under a 5s poll otherwise.
 */
export function sortLoops(a: LiveFleetOwner, b: LiveFleetOwner): number {
  const rank = (o: LiveFleetOwner) => (o.live.status === "running" ? 0 : 1);
  return (
    rank(a) - rank(b) ||
    a.agentName.localeCompare(b.agentName) ||
    a.strategyName.localeCompare(b.strategyName)
  );
}

/**
 * Every live loop, folded by the agent that owns it — the fold `DockExecution`
 * and `LoopsPanel` both need to draw one group per agent rather than a flat
 * list (FEAT-125), extracted here so it is written once.
 */
export function groupLoopsByAgent(
  loops: LiveFleetOwner[],
): Map<string, LiveFleetOwner[]> {
  const map = new Map<string, LiveFleetOwner[]>();
  for (const loop of loops) {
    const existing = map.get(loop.agentSlug);
    if (existing) existing.push(loop);
    else map.set(loop.agentSlug, [loop]);
  }
  return map;
}

export interface LoopCardStats {
  latestSessionPnl: number;
  sessionCount: number;
  experimentCount: number;
}

/** `null` when the two 5s polls have not agreed yet — the card degrades, it does not guess. */
export function loopStats(
  loop: LiveFleetOwner,
  agents: AgentSummary[],
): LoopCardStats | null {
  const strategy = agents
    .find((a) => a.slug === loop.agentSlug)
    ?.strategies.find((s) => s.slug === loop.strategySlug);
  return strategy
    ? {
        latestSessionPnl: strategy.latest_session_pnl,
        sessionCount: strategy.session_count,
        experimentCount: strategy.experiment_count,
      }
    : null;
}

/**
 * Every strategy with a live tick loop, across every agent (FEAT-123).
 *
 * `["fleet-map"]` is `useFleetData`'s own key — `GET /agents/fleet-map` makes
 * no Hummingbot call and is already pollable, so mounting this alongside the
 * fleet browser or the Execution panel shares one 5s poll rather than doubling
 * it. `live` is `fleet-map`'s own definition of "running" (the `LoopSupervisor`
 * engine registry), not a second liveness rule derived here.
 */
export function useLiveLoops(): { loops: LiveFleetOwner[]; isLoading: boolean } {
  const { data, isLoading } = useQuery({
    queryKey: ["fleet-map"],
    queryFn: () => api.getFleetMap(),
    refetchInterval: 5000,
  });
  const loops = useMemo(
    () =>
      (data?.owners ?? [])
        .filter((o): o is LiveFleetOwner => !!o.live)
        .sort(sortLoops),
    [data],
  );
  return { loops, isLoading };
}

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";
import type { FleetOwner, LiveLoop } from "@/lib/agent-attribution";

/** A `FleetOwner` whose loop is actually going — `live` narrowed off `null`. */
export type LiveFleetOwner = FleetOwner & { live: LiveLoop };

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

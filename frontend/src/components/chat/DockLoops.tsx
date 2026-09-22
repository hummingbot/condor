import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { LoopCardGrid } from "@/components/chat/LoopsPanel";
import { loopsOnServer, useLiveLoops } from "@/hooks/useLiveLoops";
import { agentsQuery } from "@/lib/queryClient";

/**
 * The desk's own Loops section (FEAT-1xx) — the same cards `LoopsPanel` draws
 * for the whole fleet, narrowed to this server.
 *
 * A third `DockSection` beside Portfolio and Execution rather than the rail's
 * old separate sheet: those two already answer "what do I hold" and "what is
 * trading" for one server, and a loop somebody is watching tick is the same
 * question asked a third way, not a different subject that deserves its own
 * overlay. `LoopCardGrid` is the one place that markup is drawn, so this
 * section and the global panel cannot render the same loop two different ways.
 *
 * Scoped through `loopsOnServer` — the rule `DockExecution` already applies to
 * its own agent rows (CORR-429) — because `fleet-map` itself answers for every
 * server at once and this section's whole contract, like its siblings', is one
 * server.
 *
 * Mounted only while the section is open (see `DockSection`): its own
 * `useLiveLoops`/`agentsQuery` calls share the query keys `DockExecution` and
 * the global panel already hold, so a reader with either open pays nothing
 * extra to open this one too.
 */
export function DockLoops({
  server,
  onOpenLoop,
}: {
  server: string;
  onOpenLoop: (agentSlug: string, strategySlug: string) => void;
}) {
  const { loops, isLoading } = useLiveLoops();
  const { data: agents = [] } = useQuery(agentsQuery());
  const scoped = useMemo(
    () => loopsOnServer(loops, agents, server),
    [loops, agents, server],
  );

  return (
    <div className="flex flex-col gap-3 p-3">
      <LoopCardGrid
        loops={scoped}
        isLoading={isLoading}
        agents={agents}
        onOpenLoop={onOpenLoop}
        emptyLabel={`Nothing looping on ${server}`}
      />
    </div>
  );
}

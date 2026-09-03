import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * What every agent is doing, on one screen (FEAT-104).
 *
 * A stub in this commit: step 1 of the feature is the mount, and the rows are
 * step 2. What it establishes is that `/` can render something other than the
 * conversation, and that the shell decides its padding, its keys overlay and
 * its ⌘K target from the *view* rather than from the pathname.
 *
 * Owns its own scrolling — `main` is full bleed on this route under either
 * view (`lib/homeView.ts`).
 */
export function FleetOverview() {
  const { data: agents = [] } = useQuery({
    // The key and the interval the chat rail already polls, so react-query
    // dedupes rather than fetching `/agents` twice.
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });

  return (
    <div className="h-full min-h-0 overflow-y-auto p-6">
      <h1 className="text-lg font-semibold">Fleet</h1>
      <ul className="mt-3 space-y-1 text-sm text-[var(--color-text-muted)]">
        {agents.map((agent) => (
          <li key={agent.slug} data-fleet-row>
            {agent.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

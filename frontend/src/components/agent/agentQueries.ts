import type { QueryClient } from "@tanstack/react-query";

import { agentQuery } from "@/lib/queryClient";

/**
 * The named invalidation sets of the agent area.
 *
 * Each mutation that changes an agent's strategies or ownership makes several
 * readers stale at once. Writing the set inline at every call site is how one
 * site ends up missing a key (PERF-343), so each set is spelled here once and
 * every mutation calls it by name.
 */

/**
 * What a start/stop/pause/resume just made stale.
 *
 * Two keys, not one. The strategy's own record is the obvious half; the agent
 * detail is the half that used to be missed, and it is load-bearing because
 * `strategies[].status` is what the workspace reads for the "Live" badge and
 * the delete guard — and what re-arms the gated `["agent", slug]` poll
 * (PERF-343). Invalidating only the strategy left an idle agent's gate closed
 * over a loop that had just started, with nothing left to reopen it.
 */
export function invalidateLifecycle(
  queryClient: QueryClient,
  slug: string,
  sslug: string,
) {
  queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
  queryClient.invalidateQueries({ queryKey: agentQuery(slug).queryKey });
}

/**
 * What creating or deleting a strategy just made stale.
 *
 * Both catalogues count strategies — the agent detail and the knowledge
 * panel's own `["agent-brain", slug]` query — so both are re-read after a
 * change.
 */
export function invalidateStrategyCatalog(
  queryClient: QueryClient,
  slug: string,
) {
  queryClient.invalidateQueries({ queryKey: agentQuery(slug).queryKey });
  queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
}

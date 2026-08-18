import { useQuery } from "@tanstack/react-query";

import { type ServerInfo, api } from "@/lib/api";
import { useServer } from "./useServer";

/**
 * The caller's permission level on a server, as reported by the API.
 *
 * This is a *UX* signal, not an authorization decision. The backend owns
 * enforcement — `require_server_access*` in `condor/web/auth.py` plus the
 * per-route `_require_owner` gate in `condor/web/routes/settings.py` — and it
 * answers 403 whatever the client believes. What this hook buys is honesty in
 * the UI: an action the API will refuse should not be offered as if it worked.
 *
 * The value comes from the same `get_server_permission()` the backend gate
 * consults (serialized on every `ServerInfo`), so the client never re-derives
 * the rule; it only reads the answer.
 */

/** Marker returned by the API for a server the caller owns (Condor admins included). */
const OWNER = "owner";

/** Explanation shown on controls that only a server owner may use. */
export const OWNER_ONLY_HINT =
  "Only the server's owner can change this — you have trader access to this server.";

export interface ServerPermissionState {
  /** "owner" | "trader", or null while the server list is still unresolved. */
  permission: string | null;
  /**
   * Whether owner-only controls should be offered. Unknown means *allowed*:
   * the client's job is to predict a refusal, never to invent one, so a slow
   * or failed server list degrades to the pre-existing behaviour and lets the
   * backend have the final word.
   */
  isOwner: boolean;
}

/**
 * Permission the current user holds on `serverName` (defaults to the selected
 * server). Shares the `["servers"]` query with the server selector, so it costs
 * a cache read rather than a request.
 */
export function useServerPermission(serverName?: string | null): ServerPermissionState {
  const { server } = useServer();
  const name = serverName ?? server;

  const { data } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    staleTime: 30_000,
  });

  const permission =
    (data as ServerInfo[] | undefined)?.find((s) => s.name === name)?.permission ?? null;

  return { permission, isOwner: permission === null || permission === OWNER };
}

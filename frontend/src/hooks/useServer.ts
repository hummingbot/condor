import { createContext, useContext } from "react";

export interface ServerContextValue {
  server: string | null;
  /** `null` clears the selection — the selected server no longer exists. */
  setServer: (s: string | null) => void;
}

/**
 * The server to land on when nothing usable is selected: the user's default if
 * it is up, otherwise the first server that is, otherwise none.
 *
 * One rule for the two places that need it — the header's auto-select, and
 * Settings after the active server is deleted — so they cannot disagree about
 * where a user ends up (FU-09).
 */
export function fallbackServer(
  servers: readonly { name: string; online: boolean; is_default?: boolean }[],
): string | null {
  const online = servers.filter((s) => s.online);
  return (online.find((s) => s.is_default) ?? online[0])?.name ?? null;
}

export const ServerContext = createContext<ServerContextValue>({
  server: null,
  setServer: () => {},
});

export function useServer() {
  return useContext(ServerContext);
}

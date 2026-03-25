import { createContext, useContext } from "react";

export interface ServerContextValue {
  server: string | null;
  setServer: (s: string) => void;
}

export const ServerContext = createContext<ServerContextValue>({
  server: null,
  setServer: () => {},
});

export function useServer() {
  return useContext(ServerContext);
}

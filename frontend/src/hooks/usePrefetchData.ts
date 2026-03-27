import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

/**
 * Prefetches core data (executors, bots) when the app loads so pages
 * render instantly instead of showing a loading state on first visit.
 */
export function usePrefetchData() {
  const { server } = useServer();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!server) return;

    queryClient.prefetchQuery({
      queryKey: ["executors", server, ""],
      queryFn: () => api.getExecutors(server),
    });

    queryClient.prefetchQuery({
      queryKey: ["bots", server],
      queryFn: () => api.getBots(server),
    });
  }, [server, queryClient]);
}

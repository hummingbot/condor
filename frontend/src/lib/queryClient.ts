import { QueryClient } from "@tanstack/react-query";

/**
 * App-wide TanStack Query cache.
 *
 * Module-scope singleton (rather than created inside `App`) so that non-React
 * code can reach it — notably the logout path in `lib/auth.ts`, which must drop
 * every cached response before the next user takes over the tab.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
    },
  },
});

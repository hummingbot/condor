import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export const RELAUNCH_KEY = ["relaunch"] as const;

/** How often to ask. Cheap in-memory read server-side; nobody is in a hurry. */
const POLL_MS = 30_000;

/**
 * Whether the server is running older code than it has on disk.
 *
 * Shared by the two surfaces that render it: the banner in the shell and the
 * finished view in Settings → Updates. One query key, so the admin who just ran
 * the update and the banner above them cannot disagree.
 *
 * Polled rather than pushed. The flag flips at most once in a process's life
 * and clears only by that process ending, so a socket channel would be a lot of
 * machinery for one bit — and the poll doubles as how the banner notices the
 * relaunch actually happened: the answer comes back `required: false`.
 */
export function useRelaunch() {
  return useQuery({
    queryKey: RELAUNCH_KEY,
    queryFn: api.getRelaunch,
    refetchInterval: POLL_MS,
    // Through the relaunch the server is down; giving up would strand the
    // banner on screen with no way back.
    retry: true,
  });
}
